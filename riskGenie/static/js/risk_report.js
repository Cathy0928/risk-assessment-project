/* Risk Report：只讀取既有 API（/api/risk-assessments、/api/risk-assessments/assets），
   不寫入任何資料；所有文字一律以 textContent 輸出。
   不包含任何寫死的示範數字 —— 每個數字都來自這兩個既有的唯讀端點。 */
(function () {
    'use strict';

    var root = document.getElementById('riskReportRoot');
    if (!root) { return; }

    var Agg = window.RiskAggregations;

    var URLS = {
        assessments: root.dataset.assessmentsUrl,
        assets: root.dataset.assetsUrl,
        detail: root.dataset.detailUrl
    };

    var DUE_WINDOW_DAYS = 14;
    var HIGH_RISK_LIMIT = 10;
    var DUE_LIMIT = 8;

    var STATUS_BADGE = {
        '待處理': 'badge--warning',
        '處理中': 'badge--info',
        '待確認': 'badge--primary',
        '已完成': 'badge--success'
    };
    var STATUS_ORDER = ['待處理', '處理中', '待確認', '已完成'];
    var LEVEL_ORDER = ['極高風險', '高風險', '中風險', '低風險'];
    var LEVEL_TONE = {
        '極高風險': 'danger',
        '高風險': 'danger',
        '中風險': 'warning',
        '低風險': 'info'
    };

    function $(id) { return document.getElementById(id); }

    function el(tag, className, text) {
        var node = document.createElement(tag);
        if (className) { node.className = className; }
        if (text !== undefined && text !== null) { node.textContent = text; }
        return node;
    }

    function clear(node) { while (node.firstChild) { node.removeChild(node.firstChild); } }

    function riskBadge(level) {
        var rank = Agg.riskRank(level);
        var label = String(level || '未分級').replace(/\s*\(.*\)\s*$/, '');
        var cls = rank >= 3 ? 'badge--danger' : rank === 2 ? 'badge--warning' : rank === 1 ? 'badge--info' : '';
        return el('span', 'badge ' + cls, label);
    }

    function statusBadge(status) {
        var label = status || '待處理';
        return el('span', 'badge ' + (STATUS_BADGE[label] || ''), label);
    }

    function assetName(a) {
        return (a.assets && a.assets.asset_name) || ('資產 #' + a.asset_id);
    }

    function assetCode(a) { return (a.assets && a.assets.asset_id_code) || ''; }

    function detailUrl(a) { return URLS.detail + '?assessment_id=' + encodeURIComponent(a.id); }

    function showAlert(message) {
        var box = $('reportAlert');
        box.classList.remove('hidden');
        box.appendChild(el('div', 'alert alert--warning', message));
    }

    function setStat(id, value, tone) {
        var node = $(id);
        node.textContent = value;
        node.classList.remove('stat__value--danger', 'stat__value--warning');
        if (tone && value > 0) { node.classList.add('stat__value--' + tone); }
    }

    /* ============================================================
       分布：橫向 CSS bar，不引入 chart 套件
    ============================================================ */

    function renderDistribution(containerId, order, counts, toneMap) {
        var container = $(containerId);
        clear(container);

        var total = order.reduce(function (sum, key) { return sum + (counts[key] || 0); }, 0);

        if (total === 0) {
            container.appendChild(el('p', 'text-muted', '目前沒有評鑑資料。'));
            return;
        }

        var list = el('div');
        list.style.display = 'grid';
        list.style.gap = 'var(--sp-2)';

        order.forEach(function (key) {
            var count = counts[key] || 0;
            var pct = total ? Math.round((count / total) * 100) : 0;

            var row = el('div');
            row.style.display = 'grid';
            row.style.gridTemplateColumns = '96px 1fr 48px';
            row.style.alignItems = 'center';
            row.style.gap = 'var(--sp-3)';

            var label = el('span', 'text-caption', key);
            row.appendChild(label);

            var track = el('div');
            track.style.height = '8px';
            track.style.borderRadius = '999px';
            track.style.background = 'var(--c-surface-sunken)';
            track.style.overflow = 'hidden';

            var tone = (toneMap && toneMap[key]) || null;
            var fill = el('div');
            fill.style.height = '100%';
            fill.style.width = pct + '%';
            fill.style.borderRadius = '999px';
            fill.style.background = tone === 'danger' ? 'var(--c-danger)'
                : tone === 'warning' ? 'var(--c-warning)'
                : tone === 'info' ? 'var(--c-info)'
                : tone === 'success' ? 'var(--c-success)'
                : 'var(--c-primary)';
            track.appendChild(fill);
            row.appendChild(track);

            row.appendChild(el('span', 'text-caption text-nowrap', String(count)));

            list.appendChild(row);
        });

        container.appendChild(list);
        container.appendChild(el('p', 'text-caption text-muted', '共 ' + total + ' 項已評鑑資產（依最新一次評鑑）'));
    }

    function renderHighRisk(rows) {
        var body = $('highRiskBody');
        clear(body);

        if (!rows.length) {
            var tr = el('tr');
            var td = el('td', 'text-muted', '目前沒有待處理的高風險項目。');
            td.colSpan = 6;
            tr.appendChild(td);
            body.appendChild(tr);
            $('highRiskHint').textContent = '';
            return;
        }

        rows.slice(0, HIGH_RISK_LIMIT).forEach(function (a) {
            var tr = el('tr');

            var nameCell = el('td');
            nameCell.appendChild(el('div', 'table__primary', assetName(a)));
            nameCell.appendChild(el('div', 'table__secondary text-mono', assetCode(a)));
            tr.appendChild(nameCell);

            var levelCell = el('td');
            levelCell.appendChild(riskBadge(a.risk_level));
            tr.appendChild(levelCell);

            tr.appendChild(el('td', 'col-num', a.risk_score !== null && a.risk_score !== undefined ? String(a.risk_score) : '—'));

            var statusCell = el('td');
            statusCell.appendChild(statusBadge(a.status));
            tr.appendChild(statusCell);

            var days = Agg.daysUntil(a.treatment_due_date);
            var dueCell = el('td', 'text-nowrap');
            if (days === null) {
                dueCell.appendChild(el('span', 'text-faint', '未設定'));
            } else {
                dueCell.appendChild(el('span', days < 0 ? 'text-danger text-strong' : '', String(a.treatment_due_date).slice(0, 10)));
                dueCell.appendChild(el('div', 'table__secondary', Agg.dueText(days)));
            }
            tr.appendChild(dueCell);

            var actionCell = el('td', 'col-actions');
            var link = el('a', 'btn btn--sm', '處置');
            link.href = detailUrl(a);
            actionCell.appendChild(link);
            tr.appendChild(actionCell);

            body.appendChild(tr);
        });

        $('highRiskHint').textContent = rows.length > HIGH_RISK_LIMIT
            ? '顯示前 ' + HIGH_RISK_LIMIT + ' 筆，共 ' + rows.length + ' 筆'
            : '共 ' + rows.length + ' 筆';
    }

    function renderDue(rows) {
        var box = $('dueList');
        clear(box);
        if (!rows.length) {
            var empty = el('div', 'empty');
            empty.appendChild(el('p', 'empty__desc', '近期沒有即將到期或逾期的處置。'));
            box.appendChild(empty);
            return;
        }
        rows.slice(0, DUE_LIMIT).forEach(function (item) {
            var a = item.assessment;
            var row = el('div', 'list-row');
            var main = el('div', 'list-row__main');
            var name = el('a', 'table__primary', assetName(a));
            name.href = detailUrl(a);
            main.appendChild(name);
            main.appendChild(el('div', 'table__secondary', String(a.treatment_due_date).slice(0, 10) + ' · ' + (a.status || '待處理')));
            row.appendChild(main);
            row.appendChild(el('span', 'badge ' + (item.days < 0 ? 'badge--danger' : item.days <= 3 ? 'badge--warning' : ''), Agg.dueText(item.days)));
            box.appendChild(row);
        });
    }

    function renderFailure(message) {
        var body = $('highRiskBody');
        clear(body);
        var tr = el('tr');
        var td = el('td', 'text-muted', message);
        td.colSpan = 6;
        tr.appendChild(td);
        body.appendChild(tr);

        clear($('dueList'));
        var empty = el('div', 'empty');
        empty.appendChild(el('p', 'empty__desc', message));
        $('dueList').appendChild(empty);

        ['statAssessed', 'statHigh', 'statOverdue'].forEach(function (id) { $(id).textContent = '—'; });

        clear($('levelDistribution'));
        $('levelDistribution').appendChild(el('p', 'text-muted', message));
        clear($('statusDistribution'));
        $('statusDistribution').appendChild(el('p', 'text-muted', message));
    }

    function fetchJson(url) {
        return fetch(url, { headers: { 'Accept': 'application/json' }, credentials: 'same-origin' })
            .then(function (res) {
                return res.json().catch(function () { return {}; }).then(function (data) {
                    if (!res.ok || data.success === false) {
                        throw new Error(data.error || data.message || ('HTTP ' + res.status));
                    }
                    return data;
                });
            });
    }

    function summarize(assetCount, assessments) {
        var summary = Agg.summarizeRiskAssessments(assetCount, assessments, { dueWindowDays: DUE_WINDOW_DAYS });

        setStat('statAssessed', summary.assessedCount);
        if (summary.unassessed !== null) {
            $('statAssessedNote').textContent = summary.unassessed > 0 ? summary.unassessed + ' 項尚未評鑑' : '全部資產皆已評鑑';
        }
        setStat('statHigh', summary.highOpen.length, 'danger');
        setStat('statOverdue', summary.overdue.length, 'danger');

        renderHighRisk(summary.highOpen);
        renderDue(summary.dueSoon);

        renderDistribution('levelDistribution', LEVEL_ORDER, summary.levelCounts, LEVEL_TONE);

        var statusTone = {
            '待處理': 'warning',
            '處理中': 'info',
            '待確認': null,
            '已完成': 'success'
        };
        renderDistribution('statusDistribution', STATUS_ORDER, summary.statusCounts, statusTone);
    }

    var assetCount = null;

    var assetsReq = fetchJson(URLS.assets).then(function (data) {
        assetCount = (data.assets || []).length;
        setStat('statAssets', assetCount);
    }).catch(function () {
        $('statAssets').textContent = '—';
        showAlert('無法載入資產數量，其他資訊仍可使用。');
    });

    var assessReq = fetchJson(URLS.assessments).then(function (data) {
        return data.assessments || [];
    });

    Promise.all([assetsReq, assessReq.catch(function (err) { return err; })]).then(function (results) {
        var assessments = results[1];
        if (assessments instanceof Error) {
            showAlert('無法載入風險評鑑資料（' + assessments.message + '）。');
            renderFailure('風險資料暫時無法載入');
            return;
        }
        summarize(assetCount, assessments);
    });
})();
