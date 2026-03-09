use anyhow::Result;
use serde::Deserialize;
use std::path::PathBuf;

const DEFAULT_API_URL: &str = "http://localhost:8000";

#[derive(Debug)]
pub struct Config {
    pub api_url: String,
}

#[derive(Deserialize, Default)]
struct YamlConfig {
    api: Option<ApiConfig>,
}

#[derive(Deserialize, Default)]
struct ApiConfig {
    url: Option<String>,
}

impl Config {
    /// Load config with priority: flag > env > user.yaml > default.
    pub fn load(flag_url: Option<&str>) -> Result<Self> {
        // 1. CLI flag (highest priority)
        if let Some(url) = flag_url {
            return Ok(Config {
                api_url: url.trim_end_matches('/').to_string(),
            });
        }

        // 2. Environment variable
        if let Ok(url) = std::env::var("TINO_API_URL") {
            return Ok(Config {
                api_url: url.trim_end_matches('/').to_string(),
            });
        }

        // 3. ~/.tino/config/user.yaml
        if let Some(url) = Self::read_yaml_config() {
            return Ok(Config {
                api_url: url.trim_end_matches('/').to_string(),
            });
        }

        // 4. Default
        Ok(Config {
            api_url: DEFAULT_API_URL.to_string(),
        })
    }

    fn read_yaml_config() -> Option<String> {
        let home = dirs::home_dir()?;
        let path: PathBuf = home.join(".tino").join("config").join("user.yaml");
        let content = std::fs::read_to_string(path).ok()?;
        let cfg: YamlConfig = serde_yaml::from_str(&content).ok()?;
        cfg.api?.url
    }
}
