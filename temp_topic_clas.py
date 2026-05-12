import dataiku
import pandas as pd
from openai import OpenAI
import json
from llm_prompt_repository.llm_prompts.topic_classification.prompt_builder import TopicClassificationPromptBuilder
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
from typing import Any
from tenacity import retry, wait_exponential, stop_after_attempt, RetryCallState

logger = logging.getLogger(__name__)


def _log_retry(retry_state: RetryCallState) -> None:
    logger.warning(
        f"LLM call failed (attempt {retry_state.attempt_number}): "
        f"{retry_state.outcome.exception()} — retrying in {retry_state.next_action.sleep:.1f}s"
    )


REASONING_MODELS = {"gpt-5", "gpt-5-mini", "gpt-5-nano", "gpt-5.2"}
CONTEXT_INFO_COLUMN = "contextual_info"


class TopicClassification:
    """LLM-based topic classification pipeline using a Dataiku project LLM or a direct OpenAI connection."""

    def __init__(self, config: dict[str, Any]) -> None:
        """Initialise the classifier, wiring up the OpenAI client and the Dataiku LLM handle.

        Args:
            config: Recipe configuration dict containing model settings, connection name, and
                    classification parameters.
        """
        self.config = config

        client = dataiku.api_client()
        connection = client.get_connection(config["connection"])
        info = connection.get_info()

        self.client = OpenAI(api_key=info["params"]["apiKey"])
        self.model: str = config["model_name"]
        self.MAX_TOKENS: int = config["max_N_tokens"]

        project = client.get_default_project()
        full_model_name = f"openai:{config['connection']}:{config['model_name']}"
        self.llm = project.get_llm(full_model_name)

        self.prompt: str = ""

    def validate_inputs(
        self,
        text_names: list[str],
        topic_names: list[str],
        topics_df: pd.DataFrame,
        df: pd.DataFrame,
    ) -> None:
        """Verify that required columns are present in both input datasets.

        Args:
            text_names: Dataset names for the text input (used in error messages).
            topic_names: Dataset names for the topics input (used in error messages).
            topics_df: DataFrame that must contain 'topic' and 'topic_description' columns.
            df: DataFrame that must contain 'content', 'id', and optionally contextual_info.

        Raises:
            Exception: If any required columns are missing from either dataset.
        """
        use_context = "with_context" in self.config["topic_clf_use_case"]
        required_text_cols = ["content", "id", CONTEXT_INFO_COLUMN] if use_context else ["content", "id"]

        missing = [col for col in required_text_cols if col not in df.columns]
        if missing:
            raise Exception(f"ERROR: Columns {','.join(missing)} missing on dataset {text_names[0]}")
        logger.info(f"Input dataset {text_names[0]} correct")

        required_topic_cols = ["topic", "topic_description"]
        missing = [col for col in required_topic_cols if col not in topics_df.columns]
        if missing:
            raise Exception(f"ERROR: Columns {','.join(missing)} missing on dataset {topic_names[0]}")
        logger.info(f"Input dataset {topic_names[0]} correct")

    def assemble_prompt(self, topics_df: pd.DataFrame) -> str:
        """Build the system prompt from the topic list and recipe configuration.

        Stores the assembled prompt in ``self.prompt`` and also returns it.

        Args:
            topics_df: DataFrame with 'topic' and 'topic_description' columns.

        Returns:
            The fully assembled system prompt string.
        """
        topics_df = topics_df.fillna("")
        topic_list = "\n\n".join(
            f"-{row.topic}: {row.topic_description}" for _, row in topics_df.iterrows()
        )

        topic_clf_use_case: str = self.config["topic_clf_use_case"]
        prompt_params = {
            "text_description": self.config["text_description"],
            "stakeholder_related": self.config["use_stakeholder"],
            "n_max_topics": self.config["n_max_topics"],
            "topic_list": topic_list,
        }

        override_prompt: bool = self.config["override_topic_clf_prompt"]
        user_overridden_prompt: str = self.config.get("user_overriden_topic_clf_prompt", "")

        if override_prompt:
            builder = TopicClassificationPromptBuilder(
                prompt_params=prompt_params,
                topic_clf_use_case=topic_clf_use_case,
                user_overriden_prompt=user_overridden_prompt,
            )
        else:
            builder = TopicClassificationPromptBuilder(
                prompt_params=prompt_params,
                topic_clf_use_case=topic_clf_use_case,
            )

        self.prompt = builder.assemble_prompt()
        return self.prompt

    def generate_chunked_dataset(self, df: pd.DataFrame) -> pd.DataFrame:
        """Split the input DataFrame into token-bounded chunks grouped by stakeholder.

        Each chunk respects both the maximum token budget and the maximum number of
        instances per batch. When stakeholder grouping is disabled a dummy column is
        used so the rest of the logic remains uniform.

        Args:
            df: Input DataFrame containing at least 'id' and 'content' columns.

        Returns:
            DataFrame with columns: chunk_id, stakeholder_column, content (JSON string),
            chunk_count, and len.
        """
        if not self.config["use_stakeholder"]:
            df = df.copy()
            df["dummy_stakeholder_column"] = "dummy_stakeholder_value"
            self.config["stakeholder_column"] = "dummy_stakeholder_column"

        stakeholder_col: str = self.config["stakeholder_column"]
        use_context = "with_context" in self.config["topic_clf_use_case"]

        max_tokens: int = self.config["max_N_tokens"]
        max_instances_per_batch: int = self.config["max_instances_per_batch"]

        if not self.config["use_batches"]:
            max_tokens = 50_000
            max_instances_per_batch = 1

        chars_per_token: int = self.config.get("chars_per_token", 4)
        max_chars = max_tokens * chars_per_token

        chunks: list[dict] = []
        chunk_id = 0

        for stakeholder, group in df.groupby(stakeholder_col, sort=False):
            current_chunk: dict = {"chunk_id": chunk_id, stakeholder_col: stakeholder, "content": []}

            for _, row in group.iterrows():
                content: str = "" if pd.isna(row["content"]) else row["content"]

                instance: dict = {"id": row["id"], "content": content}
                if use_context:
                    instance["context"] = row[CONTEXT_INFO_COLUMN]

                chunk_too_large = (
                    len(json.dumps(current_chunk["content"], indent=2)) + len(content) >= max_chars
                    or len(current_chunk["content"]) >= max_instances_per_batch
                )

                if chunk_too_large and current_chunk["content"]:
                    chunks.append(current_chunk)
                    chunk_id += 1
                    current_chunk = {
                        "chunk_id": chunk_id,
                        stakeholder_col: stakeholder,
                        "content": [],
                    }

                current_chunk["content"].append(instance)

            chunks.append(current_chunk)

        result = pd.DataFrame(chunks)
        result["chunk_count"] = result["content"].apply(len)
        result["content"] = result["content"].apply(lambda x: json.dumps(x, indent=2))
        result["len"] = result["content"].apply(len)
        return result

    def _build_instance_payload(self, row: pd.Series) -> dict:
        """Construct the per-chunk payload dict sent to the LLM.

        Args:
            row: A single row from the chunked dataset DataFrame.

        Returns:
            Dict with 'chunk_id', 'text', and optionally 'stakeholder'.
        """
        instance: dict = {"chunk_id": row["chunk_id"], "text": row["content"]}
        if self.config["use_stakeholder"]:
            instance["stakeholder"] = row[self.config["stakeholder_column"]]
        return instance

    @retry(wait=wait_exponential(multiplier=1, min=2, max=60), stop=stop_after_attempt(4), before_sleep=_log_retry)
    def _run_single(self, row: pd.Series) -> str:
        """Classify a single chunk via the Dataiku LLM abstraction.

        Retries up to 4 attempts with exponential backoff (2 s → 60 s) on any error.

        Args:
            row: A row from the chunked dataset DataFrame.

        Returns:
            Raw LLM response text.
        """
        instance = self._build_instance_payload(row)

        completion = self.llm.new_completion()
        completion.with_message(self.prompt, role="system")
        completion.with_message(json.dumps([instance], ensure_ascii=False), role="user")
        completion.settings["max_tokens"] = self.MAX_TOKENS
        return completion.execute().text

    def _run_single_direct_connection(self, row: pd.Series) -> str:
        """Classify a single chunk via the direct OpenAI client (bypassing Dataiku LLM).

        Automatically sets ``reasoning_effort`` for reasoning-class models and
        ``temperature=0`` for standard models.

        Args:
            row: A row from the chunked dataset DataFrame.

        Returns:
            Raw LLM response text.
        """
        instance = self._build_instance_payload(row)

        messages = [
            {"role": "system", "content": self.prompt},
            {"role": "user", "content": json.dumps([instance], ensure_ascii=False)},
        ]

        request_kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_completion_tokens": self.MAX_TOKENS,
        }

        if self.model in REASONING_MODELS:
            request_kwargs["reasoning_effort"] = "high"
        else:
            request_kwargs["temperature"] = 0

        response = self.client.chat.completions.create(**request_kwargs)
        return response.choices[0].message.content

    def run_llm(self, df: pd.DataFrame) -> list[str]:
        """Run the LLM over all chunks in parallel and return results in order.

        Args:
            df: The chunked dataset produced by :meth:`generate_chunked_dataset`.

        Returns:
            List of raw LLM response strings, one per chunk, in chunk order.

        Raises:
            Exception: Re-raises the first exception encountered in any worker thread.
        """
        max_workers: int = self.config.get("max_workers", 16)
        rows = list(df.iterrows())
        results: list[str | None] = [None] * len(rows)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_idx = {
                executor.submit(self._run_single, row): i
                for i, (_, row) in enumerate(rows)
            }
            completed = 0
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    results[idx] = future.result()
                    completed += 1
                    logger.info(f"Chunk {idx} complete ({completed}/{len(rows)})")
                except Exception as e:
                    logger.error(
                        f"Chunk {idx} (chunk_id={df.iloc[idx]['chunk_id']}) failed after all retries: {e} — skipping"
                    )

        skipped = results.count(None)
        if skipped:
            logger.warning(f"{skipped}/{len(rows)} chunks were skipped due to errors")

        return [r for r in results if r is not None]

    def create_output_table(self, response_texts: list[str]) -> pd.DataFrame:
        """Parse LLM responses and flatten them into a tidy output DataFrame.

        Args:
            response_texts: List of JSON response strings from :meth:`run_llm`.

        Returns:
            DataFrame with columns: id, topic, keywords, summary, sentiment.
        """
        rows: list[dict] = []
        for i, resp in enumerate(response_texts):
            try:
                parsed = json.loads(resp)
                for item in parsed["output_list"]:
                    base = {"id": item["id"]}
                    for topic in item["topics"]:
                        rows.append(base | topic)
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                logger.error(f"Failed to parse response {i}: {e} — preview: {resp[:200]!r}")

        output = pd.DataFrame(rows)
        output.rename(
            columns={
                "topic_sentiment": "sentiment",
                "topic_keywords": "keywords",
                "topic_summary": "summary",
            },
            inplace=True,
        )
        return output[["id", "topic", "keywords", "summary", "sentiment"]]

    def run(self, topics_df: pd.DataFrame, df: pd.DataFrame) -> pd.DataFrame:
        """Execute the full classification pipeline end-to-end.

        Stages: assemble prompt → chunk dataset → run LLM → build output table.

        Args:
            topics_df: DataFrame of topics (must have 'topic' and 'topic_description').
            df: DataFrame of texts to classify (must have 'id' and 'content').

        Returns:
            Tidy output DataFrame with one row per (text, assigned topic) pair.
        """
        logger.info("Assembling prompt")
        self.prompt = self.assemble_prompt(topics_df)

        logger.info("Creating chunked dataset")
        self.chunked_ds = self.generate_chunked_dataset(df)

        logger.info(f"Running classification ({len(self.chunked_ds)} chunks)")
        response_texts = self.run_llm(self.chunked_ds)

        logger.info("Creating output table")
        return self.create_output_table(response_texts)
