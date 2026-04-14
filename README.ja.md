<div align="center">

  <samp>ローカルAI。スマートルーティング。あなたのナレッジ。</samp>
  <br><br>

  <a href="https://github.com/mile-chang/rag-kura">
    <img src="assets/logo.svg" alt="RAG-Kura Logo" width="500">
  </a>

</div>

> Google Gemini および Ollama 連携による、動的モデルルーティング・機能ガード・マルチプロバイダ対応のハイブリッド RAG ナレッジアシスタント。

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.135+-009688.svg)](https://fastapi.tiangolo.com/)
[![Ollama](https://img.shields.io/badge/Ollama-local_LLM-black.svg)](https://ollama.com/)
[![Gemini](https://img.shields.io/badge/Gemini-Cloud_API-1A73E8.svg)](https://aistudio.google.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

[English](README.md) | [繁體中文](README.zh-TW.md)

---

## 概要

RAG-Kura は、FastAPI、Ollama、および Google Gemini で構築されたナレッジアシスタントバックエンドです。**モデルレジストリ（Model Registry）** によるインテリジェントなリクエストルーティングを実現し、ローカルまたはクラウドプロバイダから適切なモデルバリアントの自動選択、パラメータ注入、非対応機能の自動ブロックを手動操作なしで行います。

## 主な機能

- **検索拡張生成 (RAG)** — ChromaDBを統合し、ローカルドキュメントの知識に基づいて質問に応答。
- **モダンなチャット UI (SPA)** — HTML/CSS/JS で構築されたレスポンシブなシングルページアプリケーション。
- **動的推論トグル (思考モード)** — 推論モデル向けに `parameter` と `model_switch` 戦略をサポート。
- **スマートなロード UI** — モデルの VRAM 状態をリアルタイムで検知し、ロード中のインジケーターを表示。
- **対話の永続化** — SQLite による履歴保存、タイトルの自動生成および手動編集に対応。
- **デュアルトラック API** — 外部利用者向けの同期 JSON レスポンスと、Web クライアント向けのリアルタイム Server-Sent Events (SSE) ストリーミングを両立。
- **モジュラーアーキテクチャ** — 設定、推論エンジン、HTTP ルーターを明確に分離したクリーンなバックエンド設計。
- **モデルレジストリ** — 全登録モデルの機能を一元管理し、最適なルーティングを実現。
- **セキュリティ & 機能ガード** — 非対応機能（テキスト専用モデルへの画像送信など）のリクエストを自動的にブロック。

## システムアーキテクチャ

```mermaid
graph TB
    subgraph "フロントエンド"
        UI[Chat SPA - HTML/JS]
    end

    subgraph "FastAPI バックエンド"
        API[API ルーター]
        INF[推論エンジン層]
        REG[環境設定 & モデルレジストリ]
        DB[(SQLite - 対話履歴)]
    end

    subgraph "ベクトルストア (RAG)"
        CHROMA[(ChromaDB)]
    end

    subgraph "モデル & API"
        Q2[qwen3.5:2b]
        Q4[qwen3.5:4b]
        L3[llama3.2:3b]
        P4[phi4-mini]
        P4R[phi4-mini-reasoning]
        GEM[Gemini 3 Flash / Gemma 4]
    end

    UI -->|API リクエスト / SSE| API
    API --> DB
    API --> REG
    API --> INF
    INF -->|類似度検索| CHROMA
    INF -->|自動ルーティング| Q2
    INF -->|自動ルーティング| Q4
    INF -->|直接推論| L3
    INF -->|model_switch| P4
    INF -->|思考モード| P4R
    INF -->|クラウドAPI| GEM

    style API fill:#009688
    style DB fill:#795548
    style REG fill:#2196F3
    style GEM fill:#1A73E8
```

## サポートされるプロバイダとモデル

RAG-Kura は、ローカル (Ollama) とクラウド (Gemini) 両方のバックエンドをサポートする抽象化レイヤを提供します：

### ローカル (Ollama)
| モデル | 推論戦略 | 備考 |
|--------|----------|------|
| [**qwen3.5:2b**](https://ollama.com/library/qwen3.5) | `parameter` | パラメータ注入により `Think` モード切替に対応 |
| [**qwen3.5:4b**](https://ollama.com/library/qwen3.5) | `parameter` | 同上 |
| [**llama3.2:3b**](https://ollama.com/library/llama3.2) | `none` | 標準的な対話モデル、推論切替なし |
| [**phi4-mini**](https://ollama.com/library/phi4-mini) | `model_switch` | 推論時に `phi4-mini-reasoning` へ自動切り替え |

### クラウド (Google Gemini)
| モデル | 推論戦略 | 備考 |
|--------|----------|------|
| **Gemini 3 Flash** | `thinking_level` | 推論をサポートする高速なクラウドルーティング |
| **Gemma 4 31B** | `thinking_level_optional` | Gemini API を経由する大規模推論モデル |

## 前提条件

- Python 3.12+
- [**Ollama**](https://ollama.com/) インストール済み、必要なモデルをプル済み（完全にクラウド上の Gemini で実行する場合は任意）。
- [**Google Gemini API キー**](https://aistudio.google.com/apikey)（完全にローカルの Ollama で実行する場合は任意）。
- **PyTorch (CPU 版)**：デフォルトで CPU を使用し、Ollama 用の VRAM を節約します。VRAM に余裕がある場合は、GPU 版のインストールも可能です。

## ローカル開発

```bash
# クローンとセットアップ
git clone https://github.com/mile-chang/rag-kura.git
cd rag-kura

# 環境構築
python3 -m venv .venv
source .venv/bin/activate

# 💡 リソース計画：デフォルトで CPU 版 PyTorch をインストールして VRAM を節約します。十分なリソースがある場合は、この行をスキップして直接 install -r を実行できます。
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt

# 環境設定
cp .env.example .env
# クラウド推論モデルを使用する場合は、.env を編集して GEMINI_API_KEY を貼り付けてください

# ナレッジの取り込み（オプション）
# Markdown ファイルを docs/ に配置し、以下を実行：
python ingest.py

# サーバー起動
uvicorn main:app --reload --host 0.0.0.0 --port 8000
# ブラウザで開く: http://localhost:8000
```

## ロードマップ (Roadmap)

- [x] Phase 1: 環境構築と SSD 容量の最適化
- [x] Phase 2: AI バックエンド基盤の構築 (FastAPI & Ollama)
- [x] Phase 3: ベクトルデータベースとドキュメント処理 (ローカル ChromaDB)
- [x] Phase 4: RAG コアロジックの統合 (検索・生成)
- [x] Phase 5: モダンなチャット UI (カスタム SPA 実装)
- [ ] Phase 6: コンテナ化と自動デプロイ (Docker Compose)

詳細なタスクについては `TODO.md` をご参照ください。

## API エンドポイント (RESTful)

| メソッド | エンドポイント | 説明 |
|----------|---------------|------|
| GET | `/api/conversations` | 對話一覧の取得 |
| POST | `/api/conversations` | 新規対話の作成 |
| GET | `/api/conversations/{id}` | 對話履歴の取得 |
| DELETE | `/api/conversations/{id}` | 對話の削除 |
| PATCH | `/api/conversations/{id}/title` | 對話タイトルの更新 |
| POST | `/api/conversations/{id}/messages` | メッセージ送信（モデル/推論指定可） |
| POST | `/api/upload` | ナレッジベースドキュメントのアップロード |
| GET | `/api/models/{id}/status` | モデルが VRAM / 利用可能にロードされているか確認 |
| GET | `/api/status` | プロバイダ (Ollama/Gemini) の稼働状態を取得 |

## プロジェクト構成

```
rag-kura/
├── main.py              # エンドポイント & 静的ファイルマウント
├── config.py            # 環境設定 & モデルレジストリ
├── schemas.py           # Pydantic データモデル
├── api/                 # FastAPI HTTP ルーター層
├── inference/           # 推論エンジン & SSE ジェネレーター
├── database.py          # SQLite 永続化ロジック
├── chat_history.db      # SQLite データベース（無視対象）
├── ingest.py            # ナレッジ取り込みスクリプト
├── prompts.py           # システムプロンプトテンプレート
├── tools.py             # 外部ツールの定義
├── static/              # フロントエンド（index.html, script.js）
├── docs/                # ナレッジソースディレクトリ
├── chroma_db/           # ChromaDB ベクトルストア（無視対象）
├── requirements.txt     # Python 依存パッケージ
├── README.ja.md         # 日本語ドキュメント
└── TODO.md              # プロジェクトロードマップ
```

## 技術スタック

| 分類 | 技術 / モデル | 説明 |
|------|--------------|------|
| **チャット UI** | Vanilla JS, Tailwind CSS | モダンな SPA、生成の中断と履歴保存に対応 |
| **バックエンド核心** | FastAPI, SQLite | 非同期推論、動的ルーティング、永続化に対応 |
| **知識検索 (RAG)** | LangChain, ChromaDB | ローカルベクトルストア、Markdown 形式に対応 |
| **埋め込みモデル** | [**bge-small-zh-v1.5**](https://huggingface.co/BAAI/bge-small-zh-v1.5) | **CPU 実行**、SOTA 中文埋め込み技術、VRAM 節約 |
| **推論エンジン** | [**Ollama**](https://ollama.com/) / **Google Gemini** | ハイブリッド（ローカル/クラウド）推論ランタイムおよび関数呼び出し（Tool Calling） |

## セキュリティ

- `X-Client-ID` ヘッダーによるブラウザレベルの分離。
- タイトル文字数制限（100文字）とバックエンドでのサニタイズ。
- アップロード関連の不正パスアクセス防止。

## ライセンス

本プロジェクトは MIT ライセンスの下で公開されています — 詳細は [LICENSE](LICENSE) ファイルをご覧ください。
