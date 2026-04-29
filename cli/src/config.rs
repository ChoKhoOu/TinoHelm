use anyhow::{Context, Result};
use serde::Deserialize;
use std::path::PathBuf;

const DEFAULT_API_URL: &str = "http://localhost:8000";

#[derive(Debug, Clone)]
pub struct Config {
    pub api_url: String,
    pub api_key: Option<String>,
    pub api_key_source: ApiKeySource,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ApiKeySource {
    Flag,
    Env,
    CredentialsFile(PathBuf),
    UserYaml,
    None,
}

impl ApiKeySource {
    pub fn label(&self) -> &'static str {
        match self {
            ApiKeySource::Flag => "flag",
            ApiKeySource::Env => "env",
            ApiKeySource::CredentialsFile(_) => "credentials_file",
            ApiKeySource::UserYaml => "user_yaml",
            ApiKeySource::None => "none",
        }
    }
}

#[derive(Deserialize, Default)]
struct YamlConfig {
    api: Option<ApiConfig>,
}

#[derive(Deserialize, Default)]
struct ApiConfig {
    url: Option<String>,
    key: Option<String>,
    key_file: Option<PathBuf>,
}

impl Config {
    /// Load config with priority: flags > env > credentials file > user.yaml > default.
    pub fn load(flag_url: Option<&str>, flag_api_key: Option<&str>) -> Result<Self> {
        let yaml = Self::read_yaml_config();

        let api_url = if let Some(url) = flag_url {
            url.to_string()
        } else if let Ok(url) = std::env::var("TINO_API_URL") {
            url
        } else if let Some(url) = yaml
            .as_ref()
            .and_then(|cfg| cfg.api.as_ref())
            .and_then(|api| api.url.clone())
        {
            url
        } else {
            DEFAULT_API_URL.to_string()
        }
        .trim_end_matches('/')
        .to_string();

        let (api_key, api_key_source) = if let Some(key) = flag_api_key.and_then(Self::clean_key) {
            (Some(key), ApiKeySource::Flag)
        } else if let Ok(key) = std::env::var("TINO_API_KEY") {
            match Self::clean_key(&key) {
                Some(key) => (Some(key), ApiKeySource::Env),
                None => (None, ApiKeySource::None),
            }
        } else if let Some((path, key)) = Self::read_credentials_file(yaml.as_ref())? {
            (Some(key), ApiKeySource::CredentialsFile(path))
        } else if let Some(key) = yaml
            .as_ref()
            .and_then(|cfg| cfg.api.as_ref())
            .and_then(|api| api.key.as_deref())
            .and_then(Self::clean_key)
        {
            (Some(key), ApiKeySource::UserYaml)
        } else {
            (None, ApiKeySource::None)
        };

        Ok(Self {
            api_url,
            api_key,
            api_key_source,
        })
    }

    pub fn auth_label(&self) -> &'static str {
        if self.api_key.is_some() {
            "configured"
        } else {
            "none"
        }
    }

    pub fn credentials_path() -> Option<PathBuf> {
        dirs::home_dir().map(|home| home.join(".tino").join("credentials").join("api_key"))
    }

    pub fn write_credentials(api_key: &str) -> Result<PathBuf> {
        let path = Self::credentials_path().context("Cannot determine home directory")?;
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)
                .with_context(|| format!("Cannot create {}", parent.display()))?;
        }
        std::fs::write(&path, format!("{}\n", api_key.trim()))
            .with_context(|| format!("Cannot write {}", path.display()))?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mut perms = std::fs::metadata(&path)?.permissions();
            perms.set_mode(0o600);
            std::fs::set_permissions(&path, perms)?;
        }
        Ok(path)
    }

    pub fn remove_credentials() -> Result<Option<PathBuf>> {
        let Some(path) = Self::credentials_path() else {
            return Ok(None);
        };
        if path.exists() {
            std::fs::remove_file(&path)
                .with_context(|| format!("Cannot remove {}", path.display()))?;
            Ok(Some(path))
        } else {
            Ok(None)
        }
    }

    fn read_yaml_config() -> Option<YamlConfig> {
        let home = dirs::home_dir()?;
        let path = home.join(".tino").join("config").join("user.yaml");
        let content = std::fs::read_to_string(path).ok()?;
        serde_yaml::from_str(&content).ok()
    }

    fn read_credentials_file(yaml: Option<&YamlConfig>) -> Result<Option<(PathBuf, String)>> {
        let mut paths = Vec::new();
        if let Some(path) = yaml
            .and_then(|cfg| cfg.api.as_ref())
            .and_then(|api| api.key_file.clone())
        {
            paths.push(expand_home(path));
        }
        if let Some(path) = Self::credentials_path() {
            paths.push(path);
        }

        for path in paths {
            if !path.exists() {
                continue;
            }
            let raw = std::fs::read_to_string(&path)
                .with_context(|| format!("Cannot read API key file {}", path.display()))?;
            if let Some(key) = Self::clean_key(&raw) {
                return Ok(Some((path, key)));
            }
        }
        Ok(None)
    }

    fn clean_key(value: &str) -> Option<String> {
        let trimmed = value.trim();
        if trimmed.is_empty() {
            None
        } else {
            Some(trimmed.to_string())
        }
    }
}

fn expand_home(path: PathBuf) -> PathBuf {
    let s = path.to_string_lossy();
    if s == "~" {
        return dirs::home_dir().unwrap_or(path);
    }
    if let Some(rest) = s.strip_prefix("~/") {
        if let Some(home) = dirs::home_dir() {
            return home.join(rest);
        }
    }
    path
}
