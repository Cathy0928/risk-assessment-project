/* Dashboard：只讀取既有 API（/api/risk-assessments、/api/risk-assessments/assets），
   不寫入任何資料；所有文字一律以 textContent 輸出。 */
(function () {
    'use strict';

    var root = document.getElementById('riskPanels');
    if (!root) { return; }

    var URLS = {
        assessments: root.dataset.assessmentsUrl,
        assets: root.dataset.assetsUrl,
        detail: root.dataset.detailUrl,
        assess: root.dataset.assessUrl,
        add: root.dataset.addUrl,
        report: root.dataset.reportUrl
    };

    var DUE_WINDOW_DAYS = 14;
    var PENDING_LIMIT = 8;
    var DUE_LIMIT = 6;
    var STATUS_BADGE = {
        '待處理': 'badge--warning',
        '處理中': 'badge--info',
        '待確認': 'badge--primary',
        '已完成': 'badge--success'
    };

    function $(id) { return document.getElementById(id); }

    function el(tag, className, text) {
        var node = document.createElement(tag);
        if (className) { node.className = className; }
        if (text !== undefined && text !== null) { node.textContent = text; }
        return node;
    }

    function clear(node) { while (node.firstChild) { node.removeChild(node.firstChild); } }

    function riskRank(level) {
        var s = String(level || '');
        if (s.indexOf('極高') !== -1) { return 4; }
        if (s.indexOf('高') !== -1) { return 3; }
        if (s.indexOf('中') !== -1) { return 2; }
        if (s.indexOf('低') !== -1) { return 1; }
        return 0;
    }

    function riskBadge(level) {
        var rank = riskRank(level);
        var label = String(level || '未分級').replace(/\s*\(.*\)\s*$/, '');
        var cls = rank >= 3 ? 'badge--danger' : rank === 2 ? 'badge--warning' : rank === 1 ? 'badge--success' : '';
        return el('span', 'badge ' + cls, label);
    }

    function statusBadge(status) {
        var label = status || '待處理';
        return el('span', 'badge ' + (STATUS_BADGE[label] || ''), label);
    }

    function today() {
        var d = new Date();
        return new Date(d.getFullYear(), d.getMonth(), d.getDate());
    }

    /* 'YYYY-MM-DD' → 與今天相差的天數（以本地日期計）；無效回傳 null */
    function daysUntil(dateStr) {
        if (!dateStr) { return null; }
        var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(dateStr));
        if (!m) { return null; }
        var due = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
        return Math.round((due - today()) / 86400000);
    }

    function dueText(days) {
        if (days === null) { return null; }
        if (days < 0) { return '逾期 ' + (-days) + ' 天'; }
        if (days === 0) { return '今天到期'; }
        return days + ' 天後';
    }

    function assetName(a) {
        return (a.assets && a.assets.asset_name) || ('資產 #' + a.asset_id);
    }

    function assetCode(a) { return (a.assets && a.assets.asset_id_code) || ''; }

    function detailUrl(a) { return URLS.detail + '?assessment_id=' + encodeURIComponent(a.id); }

    /* 每個資產只看最新一次評鑑（API 已依 created_at 由新到舊排序） */
    function latestPerAsset(list) {
        var seen = {};
        var out = [];
        list.forEach(function (a) {
            if (seen[a.asset_id]) { return; }
            seen[a.asset_id] = true;
            out.push(a);
        });
        return out;
    }

    function isOpen(a) { return (a.status || '待處理') !== '已完成'; }

    function showAlert(message) {
        var box = $('dashAlert');
        box.classList.remove('hidden');
        box.appendChild(el('div', 'alert alert--warning', message));
    }

    function setStat(id, value, tone) {
        var node = $(id);
        node.textContent = value;
        node.classList.remove('stat__value--danger', 'stat__value--warning');
        if (tone && value > 0) { node.classList.add('stat__value--' + tone); }
    }

    function setNextStep(text, href, label) {
        $('nextStepText').textContent = text;
        var link = $('nextStepLink');
        if (href) {
            link.href = href;
            link.textContent = label;
            link.classList.remove('hidden');
        } else {
            link.classList.add('hidden');
        }
    }

    function renderPending(rows) {
        var body = $('pendingBody');
        clear(body);
        if (!rows.length) {
            var tr = el('tr');
            var td = el('td', 'text-muted', '目前沒有未完成處置的風險。');
            td.colSpan = 6;
            tr.appendChild(td);
            body.appendChild(tr);
            $('pendingHint').textContent = '';
            return;
        }
        rows.slice(0, PENDING_LIMIT).forEach(function (a) {
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

            var days = daysUntil(a.treatment_due_date);
            var dueCell = el('td', 'text-nowrap');
            if (days === null) {
                dueCell.appendChild(el('span', 'text-faint', '未設定'));
            } else {
                dueCell.appendChild(el('span', days < 0 ? 'text-danger text-strong' : '', String(a.treatment_due_date).slice(0, 10)));
                dueCell.appendChild(el('div', 'table__secondary', dueText(days)));
            }
            tr.appendChild(dueCell);

            var actionCell = el('td', 'col-actions');
            var link = el('a', 'btn btn--sm', '處置');
            link.href = detailUrl(a);
            actionCell.appendChild(link);
            tr.appendChild(actionCell);

            body.appendChild(tr);
        });
        $('pendingHint').textContent = rows.length > PENDING_LIMIT
            ? '顯示前 ' + PENDING_LIMIT + ' 筆，共 ' + rows.length + ' 筆'
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
            row.appendChild(el('span', 'badge ' + (item.days < 0 ? 'badge--danger' : item.days <= 3 ? 'badge--warning' : ''), dueText(item.days)));
            box.appendChild(row);
        });
    }

    function renderFailure(message) {
        var body = $('pendingBody');
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
        ['statAssessed', 'statHigh', 'statOpen', 'statOverdue'].forEach(function (id) { $(id).textContent = '—'; });
        setNextStep('無法判斷待辦事項，請重新整理頁面。', null);
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
        var latest = latestPerAsset(assessments);
        var open = latest.filter(isOpen);
        var high = latest.filter(function (a) { return riskRank(a.risk_level) >= 3; });

        var withDays = open.map(function (a) { return { assessment: a, days: daysUntil(a.treatment_due_date) }; });
        var overdue = withDays.filter(function (x) { return x.days !== null && x.days < 0; });
        var dueSoon = withDays
            .filter(function (x) { return x.days !== null && x.days <= DUE_WINDOW_DAYS; })
            .sort(function (a, b) { return a.days - b.days; });

        open.sort(function (a, b) {
            var d = riskRank(b.risk_level) - riskRank(a.risk_level);
            if (d !== 0) { return d; }
            return (Number(b.risk_score) || 0) - (Number(a.risk_score) || 0);
        });

        var unassessed = assetCount === null ? null : Math.max(assetCount - latest.length, 0);

        setStat('statAssessed', latest.length);
        if (unassessed !== null) {
            $('statAssessedNote').textContent = unassessed > 0 ? unassessed + ' 項尚未評鑑' : '全部資產皆已評鑑';
        }
        setStat('statHigh', high.length, 'danger');
        setStat('statOpen', open.length);
        setStat('statOverdue', overdue.length, 'danger');

        renderPending(open);
        renderDue(dueSoon);

        var highOpen = open.filter(function (a) { return riskRank(a.risk_level) >= 3; });

        if (assetCount === 0) {
            setNextStep('還沒有任何資產，先建立資產才能進行風險評鑑。', URLS.add, '新增資產');
        } else if (!latest.length) {
            setNextStep('資產已建立，但還沒有任何風險評鑑。', URLS.assess, '開始風險評鑑');
        } else if (overdue.length) {
            setNextStep(overdue.length + ' 筆處置已逾期，請優先確認進度。', detailUrl(overdue[0].assessment), '查看最急的一筆');
        } else if (highOpen.length) {
            setNextStep(highOpen.length + ' 筆高風險尚未完成處置。', detailUrl(highOpen[0]), '處置最高風險');
        } else if (open.length) {
            setNextStep(open.length + ' 筆風險處置尚未完成。', detailUrl(open[0]), '前往處置');
        } else if (unassessed) {
            setNextStep(unassessed + ' 項資產尚未評鑑。', URLS.assess, '繼續風險評鑑');
        } else {
            setNextStep('目前沒有待處理風險。可以檢視風險報表確認整體狀況。', URLS.report, '開啟風險報表');
        }
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
            showAlert('無法載入風險評鑑資料（' + assessments.message + '）。這不影響下方的資產清單。');
            renderFailure('風險資料暫時無法載入');
            return;
        }
        summarize(assetCount, assessments);
    });
})();
