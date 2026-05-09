# 🧞 RiskGenie

> **結合情資之 AI 資安風險精靈**
> AI-powered ISMS Risk Advisor — 支援資產盤點、風險評鑑與風險處理三階段

---

## 一、專題定位

**支援 ISMS(Information Security Management System)運作的 AI 資安風險顧問。**

服務對象:所有導入或正在導入 ISMS 的組織人員——從中小企業到大型機構皆可使用。

### 解決什麼問題?

許多組織導入 ISO 27001 ISMS 時遇到的共通痛點:

1. 資產盤點完成後,**CIA 評估高度依賴個人經驗**
2. 不同部門 / 不同人員對資產等級的判斷標準**不一致**
3. 風險評鑑常是**靜態、年度作業**,跟不上威脅變化
4. 算出風險分數後,**管理者不知道該採取哪個控制**
5. ISO 27001 / 27002 控制項繁多,**新手難以對應**

### 我們做什麼?

> 一套結合**生成式 AI**、**規則引擎**與 **RAG**,協助 ISMS 流程中
> **資產盤點 → 風險評鑑 → 風險處理** 三階段的智能顧問系統。

---

## 二、系統邊界

### ✅ 包含

- 上傳資產清冊(Excel)
- AI 建議資產類型與 CIA(主題一)
- 規則引擎計算風險分數(主題二)
- 威脅情資觸發 → 動態風險調整(主題二亮點)
- AI 顧問檢索 ISO 27002:2022 條文並生成白話建議(主題三)
- Streamlit 介面,可匯出結果

### ❌ 不包含

- 即時入侵偵測 / SIEM / SOAR
- 漏洞掃描
- 自訓練 LLM
- 完整企業級 GRC 平台

---

## 三、系統架構

```
使用者上傳 Excel 資產清冊
        ↓
   [資料解析(pandas)]
        ↓
   [主題一:AI Tagger]
   - LLM(Gemini)/ rule-based fallback
   - 不覆蓋人工值,給「建議值」+ 差異標記
        ↓
   [主題二:Risk Engine]
   - Impact = (0.4C + 0.3I + 0.3A) × 10
   - Final = clamp(Impact × LabelMult × ThreatMult, 0, 100)
        ↓
   [主題二亮點:Threat Feed]
   - 模擬 CVE / 資安事件
   - 動態升級 threat_level → 重算分數
        ↓
   [主題三:AI Advisor (RAG)]
   - 語意檢索 ISO 27002:2022 知識庫
   - Gemini 生成白話建議 / template fallback
        ↓
   [Streamlit Dashboard]
```

### 技術選型

| 層 | 技術 | 備註 |
|---|---|---|
| Frontend | Streamlit | 一個檔搞定 UI |
| LLM | Google Gemini 1.5 Flash | 免費額度足夠 demo |
| 檢索 | sentence-transformers (multilingual) | 支援中文語意 |
| Fallback | TF-IDF + char n-gram → keyword | 沒裝 ST 也能跑 |
| Data | pandas + openpyxl | |
| 知識庫 | ISO 27002:2022(JSON,可擴充) | |

---

## 四、目錄結構

```
risk-genie/
├── app.py                      # Streamlit 主程式
├── requirements.txt
├── .env.example                # 複製成 .env 並填 GEMINI_API_KEY
├── README.md
│
├── modules/
│   ├── __init__.py
│   ├── ai_tagger.py            # 主題一:AI 建議 CIA
│   ├── risk_engine.py          # 主題二:規則引擎
│   ├── threat_feed.py          # 主題二亮點:威脅情資
│   └── advisor.py              # 主題三:RAG + LLM
│
├── data/
│   ├── assets.xlsx             # 原始資產清冊(35 筆)
│   ├── assets_demo.xlsx        # Demo 用(38 筆,含低風險範例)
│   └── iso_27002_kb.json       # ISO 27002:2022 控制措施知識庫
│
└── tests/
    └── test_pipeline.py        # 回歸測試
```

---

## 五、安裝與執行

### 1. 安裝

```bash
git clone <this-repo>
cd risk-genie
pip install -r requirements.txt
```

### 2. (可選)設定 Gemini API

複製 `.env.example` 為 `.env`,填入:

```
GEMINI_API_KEY=your_key_here
```

> **沒設也能跑** — 系統會自動 fallback 到規則引擎與模板輸出,不會崩潰。

### 3. 啟動

```bash
streamlit run app.py
```

開啟瀏覽器 → 上傳 `data/assets_demo.xlsx`(建議用這份做 demo)→ 跟著畫面 5 步驟操作。

### 4. 跑測試

```bash
pytest tests/
# 或
python tests/test_pipeline.py
```

---

## 六、Demo 劇本(答辯用)

| 步驟 | 操作 | 評審看到什麼 |
|---|---|---|
| ① | 上傳 `assets_demo.xlsx` | 38 筆資產一覽 |
| ② | 點「AI Tagger」 | AI 給每筆建議 CIA + 與人工值的 delta;訂單資料庫 delta=4 是亮點 |
| ③ | 點「計算風險分數」 | 風險分布:18 Critical / 18 High / 1 Medium / 1 Low |
| ④ | 選 `CVE-2024-DEMO-SQLI` → 觸發 | **訂單資料庫 79 → 99,郵件資料庫 79 → 99** ⭐ 戲劇性 |
| ⑤ | 展開高風險資產的 AI 顧問建議 | 看到 ISO 27002 條文檢索 + 白話建議 |

---

## 七、設計原則(答辯重點)

### 1. AI 與規則的分工

> AI 給 **CIA / 威脅判斷 / 文字建議**,規則引擎算 **總分**。

理由:
- **可解釋** — 每一分都能追溯計算過程
- **可重算** — 同樣輸入永遠同樣分數
- **可審計** — 符合 ISMS 對風險評鑑可重現性的要求
- **不會因 LLM 波動而結果飄移**

### 2. 不覆蓋人工值

AI Tagger 將建議值另存到 `ai_c / ai_i / ai_a` 三欄,而非覆蓋使用者填的 CIA。
這讓系統可以:
- 比對人工 vs AI,找出**評估誤差較大的資產**
- 保留稽核軌跡
- 不違反 ISMS「人員為主、工具為輔」的原則

### 3. 威脅情資與基礎評估解耦

`Final Score = Impact × LabelMult × ThreatMult`

外部威脅只調整最後的 multiplier,不污染 CIA。當威脅事件結束時可以一鍵還原。

### 4. 多層 Fallback 確保 Demo 不會崩

| 模組 | 首選 | Fallback 1 | Fallback 2 |
|---|---|---|---|
| AI Tagger | Gemini LLM | 規則式關鍵字 | — |
| Advisor 檢索 | sentence-transformers | TF-IDF | keyword overlap |
| Advisor 生成 | Gemini LLM | 模板輸出 | — |

任何環境(包括離線、沒 API key、沒 GPU)都能跑完整 demo。

---

## 八、與夥伴版本的差異

本版本基於 master 分支整理而來,主要修改:

- 🐛 修復 `risk_engine` 對中文 label / 大小寫 threat_level 全部 fallback 1.0 的 bug
- 🔄 重構 `ai_tagger` 為「建議模式」(不覆蓋人工值)+ LLM/rule 雙軌
- ➕ 新增 `threat_feed.py`(主題二亮點,原版缺)
- 🆙 升級 `advisor`:從 keyword filter 改為**真的 RAG**(語意檢索)
- 🆙 ISO 知識庫從 8 條擴充至 26 條
- 🧹 清除根目錄的 `1XXX.py`、`SE`、`api`、`flask_app.py`、Supabase / MySQL 殘餘
- 📋 補齊 `requirements.txt`、加 `.env.example`、加測試

---

## 九、後續可擴充項目

- [ ] 串真實 NVD/CVE API(`threat_feed.py` 已預留介面)
- [ ] 報告匯出(PDF / docx)
- [ ] 多語系切換
- [ ] ChromaDB 持久化(目前 embeddings 存記憶體,每次重啟要重算)
- [ ] 使用者認證(Streamlit `st.experimental_user`)
- [ ] 歷史風險趨勢追蹤(時序資料庫)

---

## 十、團隊分工建議

| 角色 | 負責模組 |
|---|---|
| 架構與風險模型 | `risk_engine.py` + 公式設計 + 測試 |
| AI Tagger | `ai_tagger.py` + Prompt 調校 |
| Risk Scorer & Threat Feed | `threat_feed.py` + CVE / 情資設計 |
| AI Advisor / RAG | `advisor.py` + ISO 知識庫整理 |
| UI / 整合 / 簡報 | `app.py` + Demo 劇本 + 報告 |

---

## 十一、一句話介紹

> **RiskGenie 是一套支援 ISMS 運作的 AI 資安風險顧問,從資產盤點到風險評鑑再到處理建議,以「AI 出判斷、規則出分數、RAG 出建議」三層架構,協助組織提升風險管理的一致性、可解釋性與動態調整能力。**
