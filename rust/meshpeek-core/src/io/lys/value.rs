//! Navigating the decoded MessagePack scene state (a tree of maps, arrays and scalars).

use std::collections::HashMap;

use glam::DVec3;
use rmpv::Value;

/// Map lookup by string key (`None` for missing keys, non-maps and nil values).
pub fn get<'a>(value: &'a Value, key: &str) -> Option<&'a Value> {
    value.as_map()?.iter().find(|(k, _)| k.as_str() == Some(key)).map(|(_, v)| v).filter(|v| !v.is_nil())
}

/// Follow a dotted path such as `"objects.present.byId"`.
pub fn path<'a>(value: &'a Value, dotted: &str) -> Option<&'a Value> {
    dotted.split('.').try_fold(value, get)
}

/// A number (float or integer) as `f64`, or `default` when missing or not a number.
pub fn f64_or(value: Option<&Value>, default: f64) -> f64 {
    value
        .and_then(|v| v.as_f64().or_else(|| v.as_i64().map(|i| i as f64)).or_else(|| v.as_u64().map(|u| u as f64)))
        .unwrap_or(default)
}

/// The number at `key` of a map, or `default`.
pub fn number(value: &Value, key: &str, default: f64) -> f64 {
    f64_or(get(value, key), default)
}

/// The string at `key` of a map.
pub fn str_of<'a>(value: &'a Value, key: &str) -> Option<&'a str> {
    get(value, key)?.as_str()
}

/// Truthiness as Python sees it: missing, nil, false, 0 and "" are false.
pub fn truthy(value: &Value, key: &str) -> bool {
    match get(value, key) {
        None => false,
        Some(Value::Boolean(b)) => *b,
        Some(Value::String(s)) => !s.as_str().unwrap_or("").is_empty(),
        Some(v) if v.is_number() => f64_or(Some(v), 0.0) != 0.0,
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Map(m)) => !m.is_empty(),
        Some(_) => true,
    }
}

/// `{x, y, z}` map as a vector; missing components use `default`.
pub fn vec3(value: Option<&Value>, default: f64) -> DVec3 {
    match value {
        Some(v) => DVec3::new(number(v, "x", default), number(v, "y", default), number(v, "z", default)),
        None => DVec3::splat(default),
    }
}

/// Index a `byId` map by key once, instead of scanning it for every lookup.
pub fn index(map: Option<&Value>) -> HashMap<&str, &Value> {
    map.and_then(Value::as_map)
        .map(|m| m.iter().filter_map(|(k, v)| Some((k.as_str()?, v))).collect())
        .unwrap_or_default()
}

/// The `allIds` list as strings.
pub fn ids(list: Option<&Value>) -> Vec<&str> {
    list.and_then(Value::as_array).map(|a| a.iter().filter_map(Value::as_str).collect()).unwrap_or_default()
}

/// The decoded state as JSON (byte blobs shown as `"<N bytes>"`), for the dump tool.
pub fn to_json(value: &Value) -> serde_json::Value {
    use serde_json::Value as J;
    match value {
        Value::Nil => J::Null,
        Value::Boolean(b) => J::Bool(*b),
        Value::Integer(i) => i.as_i64().map(J::from).or_else(|| i.as_u64().map(J::from)).unwrap_or(J::Null),
        Value::F32(f) => serde_json::Number::from_f64(*f as f64).map_or(J::Null, J::Number),
        Value::F64(f) => serde_json::Number::from_f64(*f).map_or(J::Null, J::Number),
        Value::String(s) => J::String(s.as_str().unwrap_or("<invalid utf-8>").to_string()),
        Value::Binary(b) => J::String(format!("<{} bytes>", b.len())),
        Value::Array(a) => J::Array(a.iter().map(to_json).collect()),
        Value::Map(m) => J::Object(
            m.iter().map(|(k, v)| (k.as_str().map_or_else(|| k.to_string(), str::to_string), to_json(v))).collect(),
        ),
        Value::Ext(t, b) => J::String(format!("<ext {t}: {} bytes>", b.len())),
    }
}
