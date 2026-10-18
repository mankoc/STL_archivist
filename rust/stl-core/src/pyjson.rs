//! JSON as Python writes it: `json.dumps(value, ensure_ascii=False, indent=2)`
//! with dict insertion order, `repr(float)` numbers and `NaN` / `Infinity`;
//! plus `datetime.now(timezone.utc).isoformat()`.

/// A JSON value with ordered object keys.
#[derive(Debug, Clone, PartialEq)]
pub enum J {
    Null,
    Bool(bool),
    Int(i64),
    Float(f64),
    Str(String),
    Arr(Vec<J>),
    Obj(Vec<(String, J)>),
}

impl From<Option<String>> for J {
    fn from(v: Option<String>) -> Self {
        v.map_or(J::Null, J::Str)
    }
}

impl From<Option<i64>> for J {
    fn from(v: Option<i64>) -> Self {
        v.map_or(J::Null, J::Int)
    }
}

impl From<Option<f64>> for J {
    fn from(v: Option<f64>) -> Self {
        v.map_or(J::Null, J::Float)
    }
}

/// `json.dumps(value, ensure_ascii=False, indent=2)`.
pub fn dumps_indent2(v: &J) -> String {
    let mut out = String::new();
    write(v, 0, &mut out);
    out
}

fn write(v: &J, level: usize, out: &mut String) {
    match v {
        J::Null => out.push_str("null"),
        J::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
        J::Int(i) => out.push_str(&i.to_string()),
        J::Float(f) => out.push_str(&float_repr(*f)),
        J::Str(s) => write_str(s, out),
        J::Arr(items) if items.is_empty() => out.push_str("[]"),
        J::Obj(items) if items.is_empty() => out.push_str("{}"),
        J::Arr(items) => {
            out.push('[');
            for (k, item) in items.iter().enumerate() {
                out.push_str(if k == 0 { "\n" } else { ",\n" });
                indent(level + 1, out);
                write(item, level + 1, out);
            }
            out.push('\n');
            indent(level, out);
            out.push(']');
        }
        J::Obj(items) => {
            out.push('{');
            for (k, (key, item)) in items.iter().enumerate() {
                out.push_str(if k == 0 { "\n" } else { ",\n" });
                indent(level + 1, out);
                write_str(key, out);
                out.push_str(": ");
                write(item, level + 1, out);
            }
            out.push('\n');
            indent(level, out);
            out.push('}');
        }
    }
}

fn indent(level: usize, out: &mut String) {
    for _ in 0..level {
        out.push_str("  ");
    }
}

fn write_str(s: &str, out: &mut String) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{08}' => out.push_str("\\b"),
            '\u{0c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
}

/// `float.__repr__` (and `json`'s spelling of the non-finite values).
pub fn float_repr(f: f64) -> String {
    if f.is_nan() {
        return "NaN".into();
    }
    if f.is_infinite() {
        return if f > 0.0 { "Infinity".into() } else { "-Infinity".into() };
    }
    if f == 0.0 {
        return if f.is_sign_negative() { "-0.0".into() } else { "0.0".into() };
    }
    // Shortest round-trip digits, as Rust's `{:e}` gives them: "d.ddde-X".
    let sci = format!("{:e}", f.abs());
    let (mant, exp) = sci.split_once('e').expect("exponent form");
    let exp: i32 = exp.parse().expect("exponent");
    let digits: String = mant.chars().filter(|c| *c != '.').collect();
    let n = digits.len() as i32;
    let decpt = exp + 1; // digits d1.. with the point after position decpt
    let sign = if f < 0.0 { "-" } else { "" };
    if -4 < decpt && decpt <= 16 {
        let body = if decpt <= 0 {
            format!("0.{}{}", "0".repeat((-decpt) as usize), digits)
        } else if decpt >= n {
            format!("{}{}.0", digits, "0".repeat((decpt - n) as usize))
        } else {
            format!("{}.{}", &digits[..decpt as usize], &digits[decpt as usize..])
        };
        format!("{sign}{body}")
    } else {
        let m = if n == 1 { digits.clone() } else { format!("{}.{}", &digits[..1], &digits[1..]) };
        let e = decpt - 1;
        format!("{sign}{m}e{}{:02}", if e < 0 { '-' } else { '+' }, e.abs())
    }
}

/// `datetime.now(timezone.utc).isoformat()`.
pub fn utc_now_isoformat() -> String {
    let d = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default();
    let (date, time) = civil(d.as_secs() as i64);
    let micros = d.subsec_micros();
    if micros == 0 { format!("{date}T{time}+00:00") } else { format!("{date}T{time}.{micros:06}+00:00") }
}

fn civil(secs: i64) -> (String, String) {
    let days = secs.div_euclid(86_400);
    let rem = secs.rem_euclid(86_400);
    // Howard Hinnant's days-to-civil.
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    (format!("{y:04}-{m:02}-{d:02}"), format!("{:02}:{:02}:{:02}", rem / 3600, rem % 3600 / 60, rem % 60))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn floats_like_python() {
        for (f, s) in [
            (0.1234, "0.1234"),
            (1.0, "1.0"),
            (1e-5, "1e-05"),
            (0.0001, "0.0001"),
            (1e16, "1e+16"),
            (1234567890123456.0, "1234567890123456.0"),
            (-2.5, "-2.5"),
            (123.456, "123.456"),
            (1.5e300, "1.5e+300"),
            (-0.0, "-0.0"),
        ] {
            assert_eq!(float_repr(f), s, "{f}");
        }
    }

    #[test]
    fn dumps_like_python() {
        let v = J::Obj(vec![
            ("a".into(), J::Arr(vec![])),
            ("b".into(), J::Arr(vec![J::Int(1), J::Str("é\"\u{1}".into())])),
            ("c".into(), J::Obj(vec![])),
            ("d".into(), J::Null),
        ]);
        assert_eq!(dumps_indent2(&v), "{\n  \"a\": [],\n  \"b\": [\n    1,\n    \"é\\\"\\u0001\"\n  ],\n  \"c\": {},\n  \"d\": null\n}");
        assert_eq!(civil(0), ("1970-01-01".into(), "00:00:00".into()));
        assert_eq!(civil(1_792_000_000).0, "2026-10-14");
    }
}
