// Starting a job from a page or tab, and following the last one started there.
import { notifications } from "@mantine/notifications";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, unwrap } from "../api/client";
import { addJob, type Job, type JobRequest } from "./jobs";

export function useStartJob() {
  const queryClient = useQueryClient();
  const jobs = useQuery({ queryKey: ["jobs"], queryFn: async () => unwrap(await api.GET("/api/jobs")) });
  const [last, setLast] = useState<number | null>(null);
  const start = async (request: JobRequest) => {
    try {
      const job = unwrap(await api.POST("/api/jobs", { body: request }));
      queryClient.setQueryData<Job[]>(["jobs"], (old) => addJob(old, job));
      setLast(job.id);
    } catch (err) {
      notifications.show({ color: "red", title: "Could not start", message: (err as Error).message });
    }
  };
  /** The last job started here, as the job list has it now. */
  const current = (jobs.data ?? []).find((j) => j.id === last);
  return { jobs: jobs.data, start, current };
}
