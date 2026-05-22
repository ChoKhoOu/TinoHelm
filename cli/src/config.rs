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
    /// Load URL and API key config with priority: flags > env > credentials file > user.yaml > default.
    pub fn load(flag_url: Option<&str>, flag_api_key: Option<&str>) -> Result<Self> {
        let yaml = Self::read_yaml_config();
        let api_url = Self::resolve_api_url(flag_url, yaml.as_ref());
        let (api_key, api_key_source) = Self::resolve_api_key(flag_api_key, yaml.as_ref())?;

        Ok(Self {
            api_url,
            api_key,
            api_key_source,
        })
    }

    /// Load only non-secret URL config. This intentionally does not touch API key files.
    pub fn load_url_only(flag_url: Option<&str>) -> Self {
        let yaml = Self::read_yaml_config();
        Self {
            api_url: Self::resolve_api_url(flag_url, yaml.as_ref()),
            api_key: None,
            api_key_source: ApiKeySource::None,
        }
    }

    fn resolve_api_url(flag_url: Option<&str>, yaml: Option<&YamlConfig>) -> String {
        if let Some(url) = flag_url {
            url.to_string()
        } else if let Ok(url) = std::env::var("TINO_API_URL") {
            url
        } else if let Some(url) = yaml
            .and_then(|cfg| cfg.api.as_ref())
            .and_then(|api| api.url.clone())
        {
            url
        } else {
            DEFAULT_API_URL.to_string()
        }
        .trim_end_matches('/')
        .to_string()
    }

    fn resolve_api_key(
        flag_api_key: Option<&str>,
        yaml: Option<&YamlConfig>,
    ) -> Result<(Option<String>, ApiKeySource)> {
        if let Some(key) = flag_api_key.and_then(Self::clean_key) {
            Ok((Some(key), ApiKeySource::Flag))
        } else {
            if let Ok(key) = std::env::var("TINO_API_KEY") {
                if let Some(key) = Self::clean_key(&key) {
                    return Ok((Some(key), ApiKeySource::Env));
                }
            }

            if let Some((path, key)) = Self::read_credentials_file(yaml)? {
                Ok((Some(key), ApiKeySource::CredentialsFile(path)))
            } else if let Some(key) = yaml
                .and_then(|cfg| cfg.api.as_ref())
                .and_then(|api| api.key.as_deref())
                .and_then(Self::clean_key)
            {
                Ok((Some(key), ApiKeySource::UserYaml))
            } else {
                Ok((None, ApiKeySource::None))
            }
        }
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
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                std::fs::set_permissions(parent, std::fs::Permissions::from_mode(0o700))
                    .with_context(|| format!("Cannot chmod 0700 {}", parent.display()))?;
            }
        }
        #[cfg(unix)]
        {
            use std::fs::OpenOptions;
            use std::io::Write;
            use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};

            let mut file = OpenOptions::new()
                .write(true)
                .create(true)
                .truncate(true)
                .mode(0o600)
                .open(&path)
                .with_context(|| format!("Cannot write {}", path.display()))?;
            std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600))
                .with_context(|| format!("Cannot chmod 0600 {}", path.display()))?;
            file.write_all(api_key.trim().as_bytes())
                .with_context(|| format!("Cannot write {}", path.display()))?;
            file.write_all(b"\n")
                .with_context(|| format!("Cannot write {}", path.display()))?;
        }
        #[cfg(not(unix))]
        {
            std::fs::write(&path, format!("{}\n", api_key.trim()))
                .with_context(|| format!("Cannot write {}", path.display()))?;
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

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{Mutex, OnceLock};
    use std::time::{SystemTime, UNIX_EPOCH};

    static ENV_LOCK: OnceLock<Mutex<()>> = OnceLock::new();

    fn unique_home() -> PathBuf {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        std::env::temp_dir().join(format!("tino-config-test-{}-{nanos}", std::process::id()))
    }

    #[test]
    fn url_only_load_does_not_touch_bad_credentials_file() {
        let _guard = ENV_LOCK.get_or_init(|| Mutex::new(())).lock().unwrap();
        let old_home = std::env::var_os("HOME");
        let old_api_url = std::env::var_os("TINO_API_URL");
        let old_api_key = std::env::var_os("TINO_API_KEY");
        let home = unique_home();
        let config_dir = home.join(".tino").join("config");
        let bad_key_path = home.join("bad-key-dir");
        std::fs::create_dir_all(&config_dir).unwrap();
        std::fs::create_dir_all(&bad_key_path).unwrap();
        std::fs::write(
            config_dir.join("user.yaml"),
            format!(
                "api:\n  url: http://example.test:9000/\n  key_file: {}\n",
                bad_key_path.display()
            ),
        )
        .unwrap();
        std::env::set_var("HOME", &home);
        std::env::remove_var("TINO_API_URL");
        std::env::remove_var("TINO_API_KEY");

        let cfg = Config::load_url_only(None);
        assert_eq!(cfg.api_url, "http://example.test:9000");
        assert_eq!(cfg.api_key_source, ApiKeySource::None);
        assert!(Config::load(None, None).is_err());

        if let Some(home) = old_home {
            std::env::set_var("HOME", home);
        } else {
            std::env::remove_var("HOME");
        }
        if let Some(api_url) = old_api_url {
            std::env::set_var("TINO_API_URL", api_url);
        }
        if let Some(api_key) = old_api_key {
            std::env::set_var("TINO_API_KEY", api_key);
        }
        std::fs::remove_dir_all(home).ok();
    }

    #[test]
    fn blank_env_key_falls_through_to_saved_credentials() {
        let _guard = ENV_LOCK.get_or_init(|| Mutex::new(())).lock().unwrap();
        let old_home = std::env::var_os("HOME");
        let old_api_url = std::env::var_os("TINO_API_URL");
        let old_api_key = std::env::var_os("TINO_API_KEY");
        let home = unique_home();
        std::env::set_var("HOME", &home);
        std::env::remove_var("TINO_API_URL");
        std::env::set_var("TINO_API_KEY", "   ");
        Config::write_credentials("saved-key").unwrap();

        let cfg = Config::load(None, None).unwrap();
        assert_eq!(cfg.api_key.as_deref(), Some("saved-key"));
        assert!(matches!(
            cfg.api_key_source,
            ApiKeySource::CredentialsFile(_)
        ));

        if let Some(home) = old_home {
            std::env::set_var("HOME", home);
        } else {
            std::env::remove_var("HOME");
        }
        if let Some(api_url) = old_api_url {
            std::env::set_var("TINO_API_URL", api_url);
        } else {
            std::env::remove_var("TINO_API_URL");
        }
        if let Some(api_key) = old_api_key {
            std::env::set_var("TINO_API_KEY", api_key);
        } else {
            std::env::remove_var("TINO_API_KEY");
        }
        std::fs::remove_dir_all(home).ok();
    }

    #[test]
    #[cfg(unix)]
    fn write_credentials_creates_private_directory_and_file() {
        use std::os::unix::fs::PermissionsExt;

        let _guard = ENV_LOCK.get_or_init(|| Mutex::new(())).lock().unwrap();
        let old_home = std::env::var_os("HOME");
        let home = unique_home();
        std::fs::create_dir_all(&home).unwrap();
        std::env::set_var("HOME", &home);

        let path = Config::write_credentials("secret-token").unwrap();
        let dir_mode = std::fs::metadata(path.parent().unwrap())
            .unwrap()
            .permissions()
            .mode()
            & 0o777;
        let file_mode = std::fs::metadata(&path).unwrap().permissions().mode() & 0o777;

        assert_eq!(dir_mode, 0o700);
        assert_eq!(file_mode, 0o600);
        assert_eq!(std::fs::read_to_string(&path).unwrap(), "secret-token\n");

        if let Some(home) = old_home {
            std::env::set_var("HOME", home);
        } else {
            std::env::remove_var("HOME");
        }
        std::fs::remove_dir_all(home).ok();
    }
}
