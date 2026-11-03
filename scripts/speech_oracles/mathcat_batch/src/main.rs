//! Speak MathML with MathCAT, one JSON object per line.
//!
//! stdin:  {"id": "...", "mathml": "<math>...</math>"}
//! stdout: {"id": "...", "speech": {"ClearSpeak": "...", "SimpleSpeak": "..."},
//!          "chemistry": true|false, "error": null|"..."}
//!
//! argv[1] is MathCAT's Rules directory. NVDA's defaults are used (Medium verbosity, English).
//! "chemistry" is true when MathCAT's canonical MathML marks a chemical formula or equation
//! (`data-chem-formula`, `data-chem-equation`), that is, when it decided the input is chemistry.
use libmathcat::interface::{
    errors_to_string, get_spoken_text, set_mathml, set_preference, set_rules_dir,
};
use serde_json::{json, Value};
use std::io::{BufRead, Write};

const STYLES: [&str; 2] = ["ClearSpeak", "SimpleSpeak"];

fn recognised_chemistry(canonical: &str) -> bool {
    canonical.contains("data-chem-formula=") || canonical.contains("data-chem-equation=")
}

fn main() {
    let rules = std::env::args()
        .nth(1)
        .expect("usage: mathcat-batch RULES_DIR");
    if let Err(e) = set_rules_dir(&rules) {
        eprintln!("rules: {}", errors_to_string(&e));
        std::process::exit(2);
    }
    for (k, v) in [
        ("Language", "en"),
        ("Verbosity", "Medium"),
        ("TTS", "None"),
        ("Bookmark", "false"),
        ("DecimalSeparator", "Auto"),
    ] {
        if let Err(e) = set_preference(k, v) {
            eprintln!("pref {k}: {}", errors_to_string(&e));
            std::process::exit(2);
        }
    }
    let stdout = std::io::stdout();
    let mut out = stdout.lock();
    for line in std::io::stdin().lock().lines() {
        let line = line.expect("stdin");
        if line.trim().is_empty() {
            continue;
        }
        let item: Value = serde_json::from_str(&line).expect("json line");
        let mathml = item["mathml"].as_str().unwrap_or("");
        let mut speech = serde_json::Map::new();
        let mut error: Option<String> = None;
        let mut chem = false;
        for style in STYLES {
            if let Err(e) = set_preference("SpeechStyle", style) {
                eprintln!("style: {}", errors_to_string(&e));
                std::process::exit(3);
            }
            match set_mathml(mathml) {
                Err(e) => {
                    error = Some(errors_to_string(&e));
                    break;
                }
                Ok(canonical) => {
                    if style == STYLES[0] {
                        chem = recognised_chemistry(&canonical);
                    }
                    match get_spoken_text() {
                        Ok(text) => {
                            speech.insert(style.to_string(), Value::String(text));
                        }
                        Err(e) => {
                            error = Some(errors_to_string(&e));
                            break;
                        }
                    }
                }
            }
        }
        let reply = json!({"id": item["id"], "speech": speech, "chemistry": chem, "error": error});
        writeln!(out, "{}", reply).unwrap();
        out.flush().unwrap();
    }
}
