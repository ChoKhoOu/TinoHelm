use clap::ValueEnum;
use serde::Serialize;

#[derive(Clone, Copy, Debug, Eq, PartialEq, ValueEnum)]
pub enum OutputFormat {
    /// Raw machine-readable JSON (default).
    Json,
    /// Human-oriented terminal text.
    Text,
}

impl OutputFormat {
    pub fn is_json(self) -> bool {
        matches!(self, OutputFormat::Json)
    }
}

pub fn print_json<T: Serialize>(value: &T) -> anyhow::Result<()> {
    println!("{}", serde_json::to_string_pretty(value)?);
    Ok(())
}

pub fn print_json_error<T: Serialize>(value: &T) -> anyhow::Result<()> {
    eprintln!("{}", serde_json::to_string_pretty(value)?);
    Ok(())
}

pub fn print_error(err: &anyhow::Error, format: OutputFormat) {
    match format {
        OutputFormat::Json => {
            let _ = print_json_error(&serde_json::json!({
                "error": err.to_string(),
            }));
        }
        OutputFormat::Text => {
            eprintln!("error: {err}");
        }
    }
}
