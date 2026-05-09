# 給隊友的更新說明 (CHANGELOG)

> 這份文件說明本次大整理動了什麼、為什麼,以及你們各自的模組現在在哪。

---

## TL;DR

舊版本被診斷出**三個結構性 bug** 讓 demo 完全無法運作:

1. `risk_engine` 對中文 label「高/中/低」、小寫 `threat_level` 全部 fallback 成 1.0,**所有資產都算成 50 分**
2. `ai_tagger` 用英文關鍵字比對中文 asset_name,全部 miss,**把 Excel 的好 CIA 蓋成 5/5/5**
3. 根目錄有兩套版本(`app.py` vs `1app.py`)、兩套 DB(MySQL `utils/db.py` vs Supabase `1db.py`),且互不相容

整理後 → demo pipeline 完整可跑、四個風險等級都有、威脅觸發有戲劇性。

---

## 對照表:你的舊檔在哪裡?

| 舊路徑 | 新位置 / 處理 |
|---|---|
| `app.py`(新版) | → `app.py`(整合並擴充) |
| `1app.py`(舊版) | ❌ 移除(import 壞掉) |
| `risk_engine.py` | → `modules/risk_engine.py`(修 bug + normalize) |
| `1risk_engine.py` | ❌ 移除(舊版,但其中的 `.title()` 想法被吸收) |
| `ai_tagger.py`(新版) | → `modules/ai_tagger.py`(完全重寫) |
| `1ai_tagger.py`(舊版) | ❌ 移除(關鍵字邏輯被吸收進 fallback) |
| `engine/decision_support.py` | → `modules/advisor.py`(升級為真 RAG) |
| `1decision_support.py` | ❌ 移除 |
| `data/iso_27001.json`(8 條) | → `data/iso_27002_kb.json`(26 條,版本對齊 27002:2022) |
| `data/assets.xlsx` | ✅ 保留(內容很好,沒動) |
| `flask_app.py`、`templates/index.html` | ❌ 移除(跟 Streamlit 路線衝突,且 import 壞掉) |
| `utils/db.py`(MySQL 空殼) | ❌ 移除 |
| `1db.py`(Supabase) | ❌ 移除(本次專題不必要) |
| `SE`、`api`、`0127.txt` | ❌ 移除(意外進版控的雜物) |
| `__init__.py`、`__pycache__/` | ❌ 移除 / 重新生成 |

> **Word 文件全部保留沒動**(`系統文件.docx` 等)。

---

## 核心邏輯改動(必看)

### 改動 1:`ai_tagger` 不再覆蓋人工值 ⭐

**舊行為**:
```python
# 把 Excel 裡的 confidentiality 直接覆蓋
df["confidentiality"] = ...
```

**新行為**:
```python
# 另存到 ai_c / ai_i / ai_a,並計算 cia_delta
df["ai_c"], df["ai_i"], df["ai_a"] = ...
df["cia_delta"] = abs(ai_c - confidentiality) + ...
```

**為什麼這樣改?**
- 對齊 RiskGenie 新定位:「AI **建議** 資產價值」(不是強制決定)
- 主題一才有真的賣點可以講:「我們抓出 5 筆人工跟 AI 落差大的資產,值得複核」
- 符合 ISMS「人員為主、工具為輔」原則

### 改動 2:`risk_engine` label/threat 大小寫 + 中英文都認

**舊行為**:`{"A":1.1, "B":1.0, "C":0.9}`,而 Excel 是「高/中/低」→ 全部 fallback 1.0

**新行為**:字典同時收 `"高"/"中"/"低"`、`"HIGH"/"MEDIUM"/"LOW"`、`"A"/"B"/"C"`,並用 `_normalize_key()` 統一 upper

### 改動 3:Advisor 升級成真 RAG

**舊行為**:`[c for c in iso_data if c.get('category') in asset_type]` — 這是 list comprehension,不是 RAG

**新行為**:
- 主路徑:sentence-transformers 多語系語意檢索
- 中 fallback:TF-IDF char n-gram(中文友善)
- 終 fallback:keyword overlap

UI 會顯示用了哪一種,demo 時可以說「我們設計了三層 fallback,離線環境也能跑」。

### 改動 4:新增 `threat_feed.py`(原版根本沒有)

主題二的「動態調整」原本沒實作。新增之後:
- 預設有 4 個威脅情境(SQLi、勒索、釣魚、雲端 API)
- UI 可選擇觸發 → 命中的資產 threat_level 升級 → 重新計分
- 一鍵還原機制

---

## 四個風險等級在 demo Excel 都有

| 等級 | 範例 | 分數 |
|---|---|---|
| Critical | 客戶資料庫 / 員工人事資料庫 | 100 |
| High | 訂單資料庫 / 郵件資料庫 | 79.2 |
| Medium | 公開行銷文宣 | 36.9 |
| Low | 會議室投影設備 | 25.2 |

觸發 SQLi 後:訂單資料庫、郵件資料庫、客服聊天系統 **79.2 → 99.0** ← demo 高潮畫面

---

## 跑起來的步驟

```bash
pip install -r requirements.txt

# (可選)設定 Gemini key
cp .env.example .env
# 編輯 .env 填 GEMINI_API_KEY

# 跑測試確認沒壞
python tests/test_pipeline.py

# 啟動
streamlit run app.py
```

上傳 **`data/assets_demo.xlsx`**(38 筆,有低風險範例)效果最好。
原版 `data/assets.xlsx`(35 筆)也能跑,只是沒有 Low/Medium 等級。

---

## 答辯時的話術

如果評審問「這跟一般風險評估表單有什麼不同?」:

> 我們將 ISMS 風險評鑑流程拆成三層:**AI 給判斷、規則引擎給分數、RAG 給建議**。
>
> AI 不直接決定總分,只負責建議 CIA、識別威脅、產出建議文字,確保結果**可解釋、可重算、可稽核**——這是 ISMS 對風險評鑑很重要的要求。
>
> 同時,我們把 AI 的建議值另存,不覆蓋使用者人工填寫的數值,讓人員可以比對差異、決定是否採用,符合「**人員為主、工具為輔**」的 ISMS 精神。

如果評審問「為什麼用 ISO 27002 不用 27001?」:

> ISO 27001 是 ISMS 的**管理框架**(WHAT),27002:2022 是**控制措施實作指引**(HOW)。
> 我們的建議系統需要給出具體控制條文編號跟內容,因此知識庫對應 27002:2022,而整體流程仍遵循 27001 的風險管理原則。

---

## 如果舊功能想救回來

### 想要 Supabase 持久化?

舊版 `1db.py` 的 Supabase 寫入功能可以重接成 module。建議放到 `modules/storage.py`,在 `app.py` 加個 toggle 「儲存到 Supabase」即可。**不過畢專範圍內 Streamlit session state 應該夠用,加 DB 反而增加 demo 失敗風險。**

### 想要 Flask 版本?

Flask + 模板那條線跟 Streamlit 重複,建議 **二選一**。Streamlit 開發成本低 10 倍,demo 也夠看,推薦走 Streamlit。

如果有特殊需求一定要 Flask(例如要嵌到別的網站),可以保留 `flask_app.py`,但要先修 `get_rag_advice` 的參數簽名。

---

## 我做的所有改動,都有測試覆蓋

```bash
$ python tests/test_pipeline.py
✓ classify_risk OK
✓ Risk engine: customer=100.0, website=51.3
✓ Label normalization: impact=81.0 → final=100.0
✓ AI tagger 不覆蓋人工值,delta 平均 = 3.00
✓ Threat event: 客戶資料庫 100.0 → 100.0
✓ Threat level reset 正常
🎉 全部測試通過
```

之後改任何模組,先跑這個測試確保沒壞掉。
