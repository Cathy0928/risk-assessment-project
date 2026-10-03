/* Pure, DOM-free aggregation helpers shared by the Risk Report page.

   Kept separate from risk_report.js so this logic can be unit-tested
   under Node (no document/window needed) — see
   tests/js/risk_aggregations.test.js. Browser behavior is unchanged:
   risk_report.js loads this file first and reads window.RiskAggregations.
*/
(function (root) {
    'use strict';

    function riskRank(level) {
        var s = String(level || '');
        if (s.indexOf('極高') !== -1) { return 4; }
        if (s.indexOf('高') !== -1) { return 3; }
        if (s.indexOf('中') !== -1) { return 2; }
        if (s.indexOf('低') !== -1) { return 1; }
        return 0;
    }

    function isOpen(a) { return (a.status || '待處理') !== '已完成'; }

    function daysUntil(dateStr, today) {
        if (!dateStr) { return null; }
        var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(dateStr));
        if (!m) { return null; }
        var due = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
        var base = today || new Date();
        var baseMidnight = new Date(base.getFullYear(), base.getMonth(), base.getDate());
        return Math.round((due - baseMidnight) / 86400000);
    }

    function dueText(days) {
        if (days === null) { return null; }
        if (days < 0) { return '逾期 ' + (-days) + ' 天'; }
        if (days === 0) { return '今天到期'; }
        return days + ' 天後';
    }

    /* 每個資產只看最新一次評鑑。/api/risk-assessments 已依 created_at
       由新到舊排序，所以「每個 asset_id 第一次出現」就是最新一筆 ——
       這跟 Dashboard（dashboard.js 的 latestPerAsset）用的是同一個假設。
       這裡額外用 created_at 字串排序保護一次，不完全依賴呼叫端已排序，
       避免未來呼叫端排序方式改變時，這裡悄悄算錯。 */
    function latestPerAsset(list) {
        var sorted = (list || []).slice().sort(function (a, b) {
            var da = String(a.created_at || '');
            var db = String(b.created_at || '');
            if (da === db) { return 0; }
            return da < db ? 1 : -1;
        });
        var seen = {};
        var out = [];
        sorted.forEach(function (a) {
            if (seen[a.asset_id]) { return; }
            seen[a.asset_id] = true;
            out.push(a);
        });
        return out;
    }

    /* 回傳所有 Risk Report 用得到的彙總數字 / 清單，全部以
       latestPerAsset() 的結果為準，確保「已評鑑」「高風險待處理」
       「RiskOps 狀態分布」「風險等級分布」「待處理高風險清單」
       「近期處置期限」都是 unique by asset，不會因為同一資產有
       多筆歷史評鑑就被重複計入或重複列出一列。 */
    function summarizeRiskAssessments(assetCount, assessments, options) {
        var today = (options && options.today) || new Date();
        var dueWindowDays = (options && options.dueWindowDays) || 14;

        var latest = latestPerAsset(assessments);
        var open = latest.filter(isOpen);
        var highOpen = open.filter(function (a) { return riskRank(a.risk_level) >= 3; });

        var withDays = open.map(function (a) { return { assessment: a, days: daysUntil(a.treatment_due_date, today) }; });
        var overdue = withDays.filter(function (x) { return x.days !== null && x.days < 0; });
        var dueSoon = withDays
            .filter(function (x) { return x.days !== null && x.days <= dueWindowDays; })
            .sort(function (a, b) { return a.days - b.days; });

        highOpen = highOpen.slice().sort(function (a, b) {
            var d = riskRank(b.risk_level) - riskRank(a.risk_level);
            if (d !== 0) { return d; }
            return (Number(b.risk_score) || 0) - (Number(a.risk_score) || 0);
        });

        var levelCounts = {};
        latest.forEach(function (a) {
            var label = String(a.risk_level || '').trim();
            if (!label) { return; }
            levelCounts[label] = (levelCounts[label] || 0) + 1;
        });

        var statusCounts = {};
        latest.forEach(function (a) {
            var label = a.status || '待處理';
            statusCounts[label] = (statusCounts[label] || 0) + 1;
        });

        var unassessed = assetCount === null || assetCount === undefined
            ? null
            : Math.max(assetCount - latest.length, 0);

        return {
            latest: latest,
            open: open,
            highOpen: highOpen,
            overdue: overdue,
            dueSoon: dueSoon,
            levelCounts: levelCounts,
            statusCounts: statusCounts,
            unassessed: unassessed,
            assessedCount: latest.length
        };
    }

    var api = {
        riskRank: riskRank,
        isOpen: isOpen,
        daysUntil: daysUntil,
        dueText: dueText,
        latestPerAsset: latestPerAsset,
        summarizeRiskAssessments: summarizeRiskAssessments
    };

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    } else {
        root.RiskAggregations = api;
    }
})(typeof window !== 'undefined' ? window : this);
