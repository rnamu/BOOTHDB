# config.py - 環境変数・設定管理

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Supabase
    supabase_url: str
    supabase_key: str          # anon key（フロント用）
    supabase_service_key: str  # service_role key（バックエンド用）

    # CORS（フロントエンドのURL）
    frontend_url: str = "http://localhost:8080"

    # スクレイピング設定
    scrape_interval_hours: int = 24      # 価格チェック間隔（時間）
    scrape_delay_seconds: float = 2.0    # リクエスト間隔（秒）
    scrape_timeout_seconds: int = 15     # タイムアウト（秒）

    # アプリ設定
    app_env: str = "development"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
