use clap::ValueEnum;
use serde::Serialize;

#[derive(Clone, Copy, Debug, Eq, PartialEq, ValueEnum)]
pub enum OutputFormat {
    /// Human-oriented terminal text.
    Text,
    /// Raw machine-readable JSON returned by the API or typed command.
    Json,
    /// Stable LLM-oriented envelope: {ok,data,error,meta}.
    Llm,
}

impl OutputFormat {
    pub fn is_machine(self) -> bool {
        matches!(self, OutputFormat::Json | OutputFormat::Llm)
    }
}

#[derive(Debug, Serialize)]
pub struct Envelope<'a, T: Serialize> {
    pub ok: bool,
    pub data: Option<T>,
    pub error: Option<EnvelopeError>,
    pub meta: EnvelopeMeta<'a>,
}

#[derive(Debug, Serialize)]
pub struct EnvelopeError {
    pub kind: String,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub status_code: Option<u16>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub body: Option<serde_json::Value>,
}

#[derive(Debug, Serialize)]
pub struct EnvelopeMeta<'a> {
    pub schema: &'static str,
    pub command: &'a str,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub method: Option<&'a str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub path: Option<&'a str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub status_code: Option<u16>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub elapsed_ms: Option<u128>,
    pub api_url: &'a str,
    pub auth: &'a str,
}

impl<'a> EnvelopeMeta<'a> {
    pub fn new(command: &'a str, api_url: &'a str, auth: &'a str) -> Self {
        Self {
            schema: "tino.cli/v1",
            command,
            method: None,
            path: None,
            status_code: None,
            elapsed_ms: None,
            api_url,
            auth,
        }
    }
}

pub fn print_json<T: Serialize>(value: &T) -> anyhow::Result<()> {
    println!("{}", serde_json::to_string_pretty(value)?);
    Ok(())
}

pub fn print_llm_success<T: Serialize>(data: T, meta: EnvelopeMeta<'_>) -> anyhow::Result<()> {
    print_json(&Envelope {
        ok: true,
        data: Some(data),
        error: None,
        meta,
    })
}

pub fn print_llm_error(error: EnvelopeError, meta: EnvelopeMeta<'_>) -> anyhow::Result<()> {
    print_json(&Envelope::<serde_json::Value> {
        ok: false,
        data: None,
        error: Some(error),
        meta,
    })
}
