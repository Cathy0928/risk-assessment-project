/* Node unit tests for riskGenie/static/js/risk_aggregations.js.

   Run directly with `node tests/js/risk_aggregations.test.js`
   (also invoked from tests/test_risk_report_aggregation.py via
   subprocess, so this stays enforced under `py -m pytest -q`).

   Pure assert-based, no test framework / npm dependency needed.
*/
'use strict';

const assert = require('assert');
const path = require('path');

const Agg = require(path.join(
    __dirname, '..', '..', 'riskGenie', 'static', 'js', 'risk_aggregations.js'
));

function assessment(overrides) {
    return Object.assign({
        id: 1,
        asset_id: 1,
        status: '待處理',
        risk_level: '高風險',
        risk_score: 9,
        treatment_due_date: null,
        created_at: '2026-01-01T00:00:00+00:00'
    }, overrides);
}

let failures = 0;

function test(name, fn) {
    try {
        fn();
        console.log('ok   - ' + name);
    } catch (err) {
        failures += 1;
        console.error('FAIL - ' + name);
        console.error('       ' + err.message);
    }
}

/* ================================================================
   latestPerAsset: Phase 5 item D — same asset, multiple historical
   assessments. Only the newest (by created_at) should survive.
================================================================ */

test('latestPerAsset keeps only the newest row per asset_id', () => {
    const rows = [
        assessment({ id: 1, asset_id: 10, created_at: '2026-01-01T00:00:00+00:00', status: '待處理' }),
        assessment({ id: 2, asset_id: 10, created_at: '2026-03-01T00:00:00+00:00', status: '已完成' }),
        assessment({ id: 3, asset_id: 10, created_at: '2026-02-01T00:00:00+00:00', status: '處理中' }),
        assessment({ id: 4, asset_id: 20, created_at: '2026-01-15T00:00:00+00:00' })
    ];

    const latest = Agg.latestPerAsset(rows);

    assert.strictEqual(latest.length, 2, 'expected exactly one row per distinct asset_id');

    const forAsset10 = latest.find((a) => a.asset_id === 10);
    assert.strictEqual(forAsset10.id, 2, 'latest row for asset 10 must be the 2026-03-01 one (id=2)');
    assert.strictEqual(forAsset10.status, '已完成');
});

test('latestPerAsset is independent of input array order', () => {
    const rows = [
        assessment({ id: 2, asset_id: 10, created_at: '2026-03-01T00:00:00+00:00' }),
        assessment({ id: 1, asset_id: 10, created_at: '2026-01-01T00:00:00+00:00' }),
        assessment({ id: 3, asset_id: 10, created_at: '2026-02-01T00:00:00+00:00' })
    ];

    const latest = Agg.latestPerAsset(rows);
    assert.strictEqual(latest.length, 1);
    assert.strictEqual(latest[0].id, 2, 'must pick the newest regardless of array order');
});

/* ================================================================
   summarizeRiskAssessments: counts / lists must be unique by asset
================================================================ */

test('assessedCount is unique-by-asset, not a raw assessment count', () => {
    const rows = [
        assessment({ id: 1, asset_id: 10, created_at: '2026-01-01T00:00:00+00:00' }),
        assessment({ id: 2, asset_id: 10, created_at: '2026-02-01T00:00:00+00:00' }),
        assessment({ id: 3, asset_id: 10, created_at: '2026-03-01T00:00:00+00:00' }),
        assessment({ id: 4, asset_id: 20, created_at: '2026-01-01T00:00:00+00:00' })
    ];

    const summary = Agg.summarizeRiskAssessments(5, rows);
    assert.strictEqual(summary.assessedCount, 2, '3 historical rows for one asset must count as 1 assessed asset');
    assert.strictEqual(summary.unassessed, 3, '5 total assets - 2 assessed = 3 unassessed');
});

test('an asset with an old open high-risk row and a new completed row is not double counted as open high-risk', () => {
    const rows = [
        assessment({ id: 1, asset_id: 10, created_at: '2026-01-01T00:00:00+00:00', status: '待處理', risk_level: '高風險' }),
        assessment({ id: 2, asset_id: 10, created_at: '2026-02-01T00:00:00+00:00', status: '已完成', risk_level: '高風險' })
    ];

    const summary = Agg.summarizeRiskAssessments(1, rows);

    assert.strictEqual(summary.highOpen.length, 0,
        'the asset\'s LATEST assessment is 已完成, so it must not appear in the open high-risk list, ' +
        'even though an older row for the same asset was still 待處理');
});

test('high-risk list has at most one row per asset_id', () => {
    const rows = [
        assessment({ id: 1, asset_id: 10, created_at: '2026-01-01T00:00:00+00:00', status: '待處理', risk_level: '高風險' }),
        assessment({ id: 2, asset_id: 10, created_at: '2026-02-01T00:00:00+00:00', status: '處理中', risk_level: '極高風險' }),
        assessment({ id: 3, asset_id: 20, created_at: '2026-01-01T00:00:00+00:00', status: '待處理', risk_level: '高風險' })
    ];

    const summary = Agg.summarizeRiskAssessments(2, rows);
    const assetIds = summary.highOpen.map((a) => a.asset_id);

    assert.strictEqual(summary.highOpen.length, 2, 'two distinct assets, so at most two rows');
    assert.strictEqual(new Set(assetIds).size, assetIds.length, 'no asset_id should repeat in the high-risk list');

    const asset10Row = summary.highOpen.find((a) => a.asset_id === 10);
    assert.strictEqual(asset10Row.id, 2, 'must use the latest (id=2, 極高風險) row for asset 10, not the older id=1');
});

test('risk-level distribution counts each asset once, using its latest level', () => {
    const rows = [
        assessment({ id: 1, asset_id: 10, created_at: '2026-01-01T00:00:00+00:00', risk_level: '低風險' }),
        assessment({ id: 2, asset_id: 10, created_at: '2026-02-01T00:00:00+00:00', risk_level: '極高風險' }),
        assessment({ id: 3, asset_id: 20, created_at: '2026-01-01T00:00:00+00:00', risk_level: '中風險' })
    ];

    const summary = Agg.summarizeRiskAssessments(2, rows);

    assert.deepStrictEqual(summary.levelCounts, { '極高風險': 1, '中風險': 1 },
        'asset 10 must be counted under its latest level (極高風險), not its older 低風險 row');
});

test('RiskOps status distribution counts each asset once, using its latest status', () => {
    const rows = [
        assessment({ id: 1, asset_id: 10, created_at: '2026-01-01T00:00:00+00:00', status: '待處理' }),
        assessment({ id: 2, asset_id: 10, created_at: '2026-02-01T00:00:00+00:00', status: '已完成' }),
        assessment({ id: 3, asset_id: 20, created_at: '2026-01-01T00:00:00+00:00', status: '處理中' })
    ];

    const summary = Agg.summarizeRiskAssessments(2, rows);

    assert.deepStrictEqual(summary.statusCounts, { '已完成': 1, '處理中': 1 });
});

test('overdue / due-soon lists are unique by asset and based on the latest row', () => {
    const today = new Date(2026, 5, 15); // 2026-06-15, local time to match daysUntil()

    const rows = [
        assessment({
            id: 1, asset_id: 10, created_at: '2026-01-01T00:00:00+00:00',
            status: '待處理', treatment_due_date: '2026-06-01' // old row: overdue
        }),
        assessment({
            id: 2, asset_id: 10, created_at: '2026-02-01T00:00:00+00:00',
            status: '處理中', treatment_due_date: '2026-06-20' // latest row: due soon, not overdue
        })
    ];

    const summary = Agg.summarizeRiskAssessments(1, rows, { today: today });

    assert.strictEqual(summary.overdue.length, 0,
        'the latest row for asset 10 is not overdue; the older row\'s overdue date must not leak in');
    assert.strictEqual(summary.dueSoon.length, 1);
    assert.strictEqual(summary.dueSoon[0].assessment.id, 2);
});

process.exit(failures === 0 ? 0 : 1);
