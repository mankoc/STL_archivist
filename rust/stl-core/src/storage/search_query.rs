//! The browser's search box language: `field=value` (a known search field; the
//! value a word or `"quoted"`), `AND`, `OR`, `NOT` (upper case), parentheses.
//! Terms side by side are ANDed, except plain words, which stay one phrase as
//! before (`dark knight` finds "dark knight"). Text that does not parse is
//! searched as typed.

use super::queries::SEARCH_FIELDS;

#[derive(Debug, Clone, PartialEq)]
pub enum Query {
    /// Text in the chosen fields.
    Text(String),
    /// `(field, text)`.
    Field(String, String),
    Not(Box<Query>),
    And(Vec<Query>),
    Or(Vec<Query>),
}

#[derive(Debug, Clone, PartialEq)]
enum Tok {
    Word(String),
    Quoted(String),
    Field(String),
    And,
    Or,
    Not,
    Open,
    Close,
}

fn field_name(word: &str) -> Option<String> {
    let w = word.to_lowercase();
    SEARCH_FIELDS.contains(&w.as_str()).then_some(w)
}

fn tokens(text: &str) -> Vec<Tok> {
    let cs: Vec<char> = text.chars().collect();
    let mut out = Vec::new();
    let mut i = 0;
    let skip_space = |mut i: usize| {
        while i < cs.len() && cs[i].is_whitespace() {
            i += 1;
        }
        i
    };
    while i < cs.len() {
        let c = cs[i];
        if c.is_whitespace() {
            i += 1;
        } else if c == '(' || c == ')' {
            out.push(if c == '(' { Tok::Open } else { Tok::Close });
            i += 1;
        } else if c == '"' {
            let end = cs[i + 1..].iter().position(|&c| c == '"').map_or(cs.len(), |p| i + 1 + p);
            out.push(Tok::Quoted(cs[i + 1..end].iter().collect()));
            i = end + 1;
        } else {
            let start = i;
            while i < cs.len() && !cs[i].is_whitespace() && !"()\"=".contains(cs[i]) {
                i += 1;
            }
            let word: String = cs[start..i].iter().collect();
            // `title=…` or `title = …`: a field; any other `=` is part of the word.
            let eq = skip_space(i);
            if let Some(f) = field_name(&word).filter(|_| eq < cs.len() && cs[eq] == '=') {
                out.push(Tok::Field(f));
                i = skip_space(eq + 1);
                continue;
            }
            if i < cs.len() && cs[i] == '=' {
                while i < cs.len() && !cs[i].is_whitespace() && !"()\"".contains(cs[i]) {
                    i += 1;
                }
            }
            let word: String = cs[start..i].iter().collect();
            out.push(match word.as_str() {
                "AND" => Tok::And,
                "OR" => Tok::Or,
                "NOT" => Tok::Not,
                _ => Tok::Word(word),
            });
        }
    }
    out
}

struct Parser {
    toks: Vec<Tok>,
    at: usize,
}

impl Parser {
    fn peek(&self) -> Option<&Tok> {
        self.toks.get(self.at)
    }

    fn next(&mut self) -> Option<Tok> {
        let t = self.toks.get(self.at).cloned();
        self.at += 1;
        t
    }

    fn or(&mut self) -> Option<Query> {
        let mut parts = vec![self.and()?];
        while self.peek() == Some(&Tok::Or) {
            self.at += 1;
            parts.push(self.and()?);
        }
        Some(if parts.len() == 1 { parts.pop()? } else { Query::Or(parts) })
    }

    fn and(&mut self) -> Option<Query> {
        let mut parts = vec![self.not()?];
        loop {
            match self.peek() {
                None | Some(Tok::Or | Tok::Close) => break,
                Some(Tok::And) => self.at += 1,
                Some(_) => {}
            }
            let q = self.not()?;
            // Plain words side by side are one phrase.
            if let (Some(Query::Text(prev)), Query::Text(w)) = (parts.last_mut(), &q)
                && matches!(self.toks[self.at - 1], Tok::Word(_))
                && matches!(self.toks[self.at - 2], Tok::Word(_))
            {
                prev.push(' ');
                prev.push_str(w);
                continue;
            }
            parts.push(q);
        }
        Some(if parts.len() == 1 { parts.pop()? } else { Query::And(parts) })
    }

    fn not(&mut self) -> Option<Query> {
        match self.next()? {
            Tok::Not => Some(Query::Not(Box::new(self.not()?))),
            Tok::Open => {
                let q = self.or()?;
                (self.next()? == Tok::Close).then_some(q)
            }
            Tok::Word(w) | Tok::Quoted(w) => Some(Query::Text(w)),
            Tok::Field(f) => match self.next()? {
                Tok::Word(v) | Tok::Quoted(v) => Some(Query::Field(f, v)),
                _ => None,
            },
            Tok::And | Tok::Or | Tok::Close => None,
        }
    }
}

/// The query in `text` (`None`: nothing to search).
pub fn parse(text: &str) -> Option<Query> {
    if text.trim().is_empty() {
        return None;
    }
    let mut p = Parser { toks: tokens(text), at: 0 };
    match p.or() {
        Some(q) if p.at >= p.toks.len() => Some(q),
        _ => Some(Query::Text(text.trim().to_string())),
    }
}

#[cfg(test)]
mod tests {
    use super::Query::*;
    use super::*;

    fn t(s: &str) -> Query {
        Text(s.into())
    }

    fn f(k: &str, v: &str) -> Query {
        Field(k.into(), v.into())
    }

    #[test]
    fn parses_fields_and_operators() {
        assert_eq!(parse("  "), None);
        assert_eq!(parse("dark knight"), Some(t("dark knight")), "plain words stay one phrase");
        assert_eq!(parse("black and white"), Some(t("black and white")), "lower-case and is a word");
        assert_eq!(
            parse(r#"title="batman" OR creator="loot studios""#),
            Some(Or(vec![f("title", "batman"), f("creator", "loot studios")]))
        );
        assert_eq!(parse("Title = bat creator=loot"), Some(And(vec![f("title", "bat"), f("creator", "loot")])));
        assert_eq!(
            parse("dragon NOT (tags=bust OR tags=\"ter rain\")"),
            Some(And(vec![t("dragon"), Not(Box::new(Or(vec![f("tags", "bust"), f("tags", "ter rain")])))]))
        );
        assert_eq!(parse("red AND dragon"), Some(And(vec![t("red"), t("dragon")])));
        assert_eq!(parse("\"red\" dragon"), Some(And(vec![t("red"), t("dragon")])));
        assert_eq!(parse("url=https://x.com/?a=b"), Some(f("url", "https://x.com/?a=b")));
        assert_eq!(parse("a=b"), Some(t("a=b")), "not a field: a word");
        assert_eq!(parse("title=\"open quote"), Some(f("title", "open quote")));
    }

    #[test]
    fn what_does_not_parse_is_text() {
        for s in ["(dragon", "dragon)", "OR dragon", "dragon AND", "title=", "NOT"] {
            assert_eq!(parse(s), Some(t(s)), "{s}");
        }
    }
}
