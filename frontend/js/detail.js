/* ===========================================
   detail.js - 商品詳細ページ（API連携版）
   =========================================== */

'use strict';

let activeChart = null;
let currentProductId = null;

// ==========================================
// グラフ描画
// ==========================================

// バリエーション線の色パレット（最大10色、それ以上は循環使用）
const VARIATION_COLORS = [
    '#8b5cf6', '#06b6d4', '#f97316', '#ec4899', '#22c55e',
    '#eab308', '#3b82f6', '#ef4444', '#14b8a6', '#a855f7',
];

let lastSeriesData = null;

function renderPriceChart(seriesData) {
    const canvas = document.getElementById('price-chart');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');

    if (activeChart) {
        activeChart.destroy();
        activeChart = null;
    }

    const variationNames = Object.keys(seriesData || {});

    if (variationNames.length === 0) {
        canvas.closest('.chart-viewport').innerHTML = '<p class="empty-message">価格履歴がまだありません</p>';
        return;
    }

    lastSeriesData = seriesData;

    // 全系統の日付を統合してX軸ラベルを作る（日付昇順・重複なし）
    const allDatesSet = new Set();
    variationNames.forEach(function (name) {
        seriesData[name].forEach(function (h) { allDatesSet.add(h.recorded_at); });
    });
    const allDates = Array.from(allDatesSet).sort();

    const labels = allDates.map(function (d) {
        const date = new Date(d);
        return `${date.getMonth() + 1}/${date.getDate()}`;
    });

    const style  = getComputedStyle(document.documentElement);
    const gridColor  = style.getPropertyValue('--chart-grid').trim();
    const tickColor  = style.getPropertyValue('--chart-tick').trim();
    const ttBg       = style.getPropertyValue('--chart-tooltip-bg').trim();
    const ttTitle    = style.getPropertyValue('--chart-tooltip-title').trim();
    const ttBody     = style.getPropertyValue('--chart-tooltip-body').trim();
    const ttBorder   = style.getPropertyValue('--chart-tooltip-border').trim();

    // バリエーションごとにdatasetを作成。
    // 日付が記録されていない箇所は直前の価格を引き継ぐ（階段状の表示にする）
    const datasets = variationNames.map(function (name, idx) {
        const color = VARIATION_COLORS[idx % VARIATION_COLORS.length];
        const history = seriesData[name];

        const priceByDate = {};
        history.forEach(function (h) { priceByDate[h.recorded_at] = h.price; });

        let lastKnownPrice = null;
        const data = allDates.map(function (d) {
            if (priceByDate[d] !== undefined) {
                lastKnownPrice = priceByDate[d];
            }
            return lastKnownPrice;
        });

        return {
            label: name,
            data: data,
            borderColor: color,
            backgroundColor: color + '20',
            borderWidth: 2.5,
            tension: 0.15,
            fill: false,
            spanGaps: true,
            pointBackgroundColor: color,
            pointBorderColor: '#ffffff',
            pointBorderWidth: 1.5,
            pointRadius: 4,
            pointHoverRadius: 7,
        };
    });

    activeChart = new Chart(ctx, {
        type: 'line',
        data: { labels, datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    display: variationNames.length > 1,
                    position: 'top',
                    labels: {
                        color: tickColor,
                        font: { weight: 'bold', size: 11 },
                        boxWidth: 12,
                        padding: 10,
                    }
                },
                tooltip: {
                    backgroundColor: ttBg,
                    titleColor: ttTitle,
                    bodyColor: ttBody,
                    borderColor: ttBorder,
                    borderWidth: 1,
                    padding: 12,
                    callbacks: {
                        label: function (ctx) {
                            return ctx.dataset.label + ': ¥' + ctx.parsed.y.toLocaleString();
                        }
                    }
                }
            },
            scales: {
                y: {
                    grid: { color: gridColor },
                    ticks: {
                        color: tickColor,
                        font: { weight: 'bold' },
                        callback: function (v) { return '¥' + v.toLocaleString(); }
                    }
                },
                x: {
                    grid: { color: gridColor },
                    ticks: { color: tickColor, font: { weight: 'bold' } }
                }
            }
        }
    });

    // テーマ変更時にグラフを再描画
    new MutationObserver(function () {
        if (activeChart && lastSeriesData) renderPriceChart(lastSeriesData);
    }).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
}


// ==========================================
// 商品情報の表示
// ==========================================

function renderVariations(variations) {
    const card = document.getElementById('variations-card');
    const list = document.getElementById('variations-list');
    if (!card || !list) return;

    if (!variations || variations.length === 0) {
        card.style.display = 'none';
        return;
    }

    card.style.display = 'block';

    const isMultiple = variations.length > 1;
    const highest = isMultiple ? Math.max(...variations.map(v => v.price)) : null;

    list.innerHTML = variations.map(function (v) {
        const isHighest = isMultiple && v.price === highest;
        return `
            <div class="variation-row">
                <span class="variation-name">
                    ${escapeHtml(v.name)}
                    ${isHighest ? '<span class="variation-highest-badge">最高額</span>' : ''}
                </span>
                <span class="variation-price ${isHighest ? 'is-highest' : ''}">¥${v.price.toLocaleString()}</span>
            </div>
        `;
    }).join('');
}

function renderProductInfo(product) {
    const set = (id, text) => {
        const el = document.getElementById(id);
        if (el) el.textContent = text ?? '-';
    };

    set('breadcrumb-product', product.title);
    set('detail-title', product.title);
    set('detail-creator', product.creator_name || '-');
    document.title = `${product.title} | BOOTHDB`;

    // パンくずの商品名
    const crumb = document.getElementById('breadcrumb-product');
    if (crumb) crumb.textContent = product.title;

    // サムネイル
    const thumb = document.getElementById('detail-thumb');
    if (thumb) {
        if (product.thumbnail_url) {
            thumb.innerHTML = `<img src="${escapeHtml(product.thumbnail_url)}" alt="${escapeHtml(product.title)}">`;
        } else {
            thumb.textContent = product.title;
        }
    }

    // BOOTH公式リンク
    const boothLink = document.getElementById('booth-link');
    if (boothLink) boothLink.href = product.booth_url || '#';

    // 価格
    const priceEl = document.getElementById('detail-price');
    if (priceEl && product.current_price != null) {
        priceEl.textContent = `¥${product.current_price.toLocaleString()}`;
    }
}


// ==========================================
// 価格統計の表示
// ==========================================

function renderPriceStats(priceData) {
    const set = (id, val) => {
        const el = document.getElementById(id);
        if (el) el.textContent = val != null ? `¥${Number(val).toLocaleString()}` : '-';
    };
    set('stat-current', priceData.current_price);
    set('stat-lowest',  priceData.lowest_price);
    set('stat-highest', priceData.highest_price);

    const dateEl = document.getElementById('stat-lowest-date');
    if (dateEl && priceData.lowest_price_date) {
        const d = new Date(priceData.lowest_price_date);
        dateEl.textContent = `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}`;
    }
}


// ==========================================
// レビューの表示
// ==========================================

function renderReviews(reviewData) {
    // 平均評価
    const avgEl = document.getElementById('review-avg');
    if (avgEl) avgEl.textContent = reviewData.average_rating?.toFixed(1) ?? '-';

    const countEl = document.getElementById('review-count');
    if (countEl) countEl.textContent = reviewData.total;

    // 分布バー
    const dist = reviewData.rating_distribution || {};
    const total = Object.values(dist).reduce((a, b) => a + b, 0);
    for (let i = 1; i <= 5; i++) {
        const fill = document.getElementById(`dist-fill-${i}`);
        const pct  = document.getElementById(`dist-pct-${i}`);
        const count = dist[String(i)] || 0;
        const percent = total > 0 ? Math.round((count / total) * 100) : 0;
        if (fill) fill.style.width = `${percent}%`;
        if (pct)  pct.textContent = `${percent}%`;
    }

    // レビュー一覧
    const listEl = document.getElementById('review-list');
    if (!listEl) return;

    if (!reviewData.items || reviewData.items.length === 0) {
        listEl.innerHTML = '<p class="empty-message">まだレビューはありません。最初のレビューを投稿しましょう！</p>';
        return;
    }

    listEl.innerHTML = reviewData.items.map(function (r) {
        const stars = '★'.repeat(r.rating) + '☆'.repeat(5 - r.rating);
        const date = new Date(r.created_at);
        const dateStr = `${date.getFullYear()}/${date.getMonth() + 1}/${date.getDate()}`;
        return `
            <div class="review-item">
                <div class="review-meta">
                    <div class="review-user">
                        <div class="review-avatar" style="background: linear-gradient(135deg,#a78bfa,#8b5cf6);"></div>
                        <div>
                            <div class="review-username">${escapeHtml(r.username || '匿名ユーザー')}</div>
                            <div class="review-stars">${stars}</div>
                        </div>
                    </div>
                    <span class="review-date">${dateStr}</span>
                </div>
                ${r.avatar_name ? `<p class="review-avatar-tag">使用アバター: ${escapeHtml(r.avatar_name)}</p>` : ''}
                ${r.comment ? `<p class="review-body">${escapeHtml(r.comment)}</p>` : ''}
            </div>
        `;
    }).join('');
}


// ==========================================
// レビュー投稿フォーム
// ==========================================

async function loadAvatarOptions() {
    const select = document.getElementById('review-avatar-select');
    if (!select) return;

    try {
        const data = await AvatarApi.list();
        select.innerHTML = '<option value="">アバターを選択</option>'
            + data.items.map(function (a) {
                return `<option value="${escapeHtml(a.id)}">${escapeHtml(a.name)}</option>`;
            }).join('');
    } catch (err) {
        console.error('アバター一覧取得エラー:', err);
    }
}

async function handleReviewSubmit(e) {
    e.preventDefault();
    if (!Auth.isLoggedIn()) {
        showToast('レビューを投稿するにはログインが必要です', 'error');
        openModal('auth-modal');
        return;
    }

    const avatarId = document.getElementById('review-avatar-select')?.value;
    const rating   = parseInt(document.querySelector('.star-btn.selected')?.getAttribute('data-rating') || '0');
    const comment  = document.getElementById('review-comment')?.value.trim() || null;
    const submitBtn = document.getElementById('review-submit-btn');

    if (!avatarId) { showToast('使用アバターを選択してください', 'error'); return; }
    if (!rating)   { showToast('評価（星）を選択してください', 'error'); return; }

    submitBtn.disabled = true;
    submitBtn.textContent = '投稿中...';

    try {
        await ReviewApi.post({
            productId: currentProductId,
            avatarId,
            rating,
            comment,
        });
        showToast('レビューを投稿しました！', 'success');
        // レビュー再読み込み
        const reviewData = await ProductApi.getReviews(currentProductId);
        renderReviews(reviewData);
    } catch (err) {
        showToast(err.message || 'レビューの投稿に失敗しました', 'error');
    } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = 'レビューを投稿';
    }
}


// ==========================================
// 星評価ボタン
// ==========================================

function initStarButtons() {
    const buttons = document.querySelectorAll('.star-btn');
    buttons.forEach(function (btn) {
        btn.addEventListener('click', function () {
            const rating = parseInt(btn.getAttribute('data-rating'));
            buttons.forEach(function (b, i) {
                b.classList.toggle('selected', parseInt(b.getAttribute('data-rating')) <= rating);
            });
        });
        btn.addEventListener('mouseover', function () {
            const rating = parseInt(btn.getAttribute('data-rating'));
            buttons.forEach(function (b) {
                b.classList.toggle('hovered', parseInt(b.getAttribute('data-rating')) <= rating);
            });
        });
        btn.addEventListener('mouseout', function () {
            buttons.forEach(function (b) { b.classList.remove('hovered'); });
        });
    });
}


// ==========================================
// 初期化
// ==========================================

document.addEventListener('DOMContentLoaded', async function () {
    const params = new URLSearchParams(location.search);
    currentProductId = params.get('id');

    if (!currentProductId) {
        document.querySelector('main').innerHTML = '<p class="error-message">商品IDが指定されていません</p>';
        return;
    }

    // 管理者ログイン中であれば「再収集」ボタンを表示する
    await initAdminRescrapeButton();

    // 並行してデータを取得
    try {
        const [product, priceData, priceSeriesData, reviewData, variationData] = await Promise.all([
            ProductApi.get(currentProductId),
            ProductApi.getPriceHistory(currentProductId),
            ProductApi.getPriceHistoryByVariation(currentProductId),
            ProductApi.getReviews(currentProductId),
            ProductApi.getVariations(currentProductId),
        ]);

        renderProductInfo(product);
        renderPriceStats(priceData);
        renderPriceChart(priceSeriesData.series);
        renderReviews(reviewData);
        renderVariations(variationData.items);

    } catch (err) {
        showToast('商品データの読み込みに失敗しました', 'error');
        console.error(err);
    }

    // レビューフォーム初期化
    await loadAvatarOptions();
    initStarButtons();

    const reviewForm = document.getElementById('review-form');
    if (reviewForm) reviewForm.addEventListener('submit', handleReviewSubmit);

    // アバター行のクリック → アバターページへ
    document.querySelectorAll('[data-avatar-link]').forEach(function (el) {
        el.addEventListener('click', function () {
            location.href = `avatar.html?id=${encodeURIComponent(el.getAttribute('data-avatar-link'))}`;
        });
    });
});


/**
 * 管理者専用「再収集」ボタンの初期化
 *
 * 2つの管理者ログイン経路に対応する:
 * 1. admin.html でパスワードログイン → sessionStorageの管理者トークンを使う
 * 2. index.html で admin@boothdb.com としてログイン → 通常のユーザートークンを使う
 *    （ユーザー名で管理者と判定する）
 */
const ADMIN_EMAIL_WHITELIST = ['admin@boothdb.com'];

async function initAdminRescrapeButton() {
    const btn = document.getElementById('admin-rescrape-btn');
    if (!btn) return;

    // 経路1: admin.html からのパスワードログイン
    const ADMIN_TOKEN_KEY = 'boothdb_admin_token';
    const adminPanelToken = sessionStorage.getItem(ADMIN_TOKEN_KEY);

    // 経路2: index.html からの通常ログイン（admin@boothdb.com かどうか確認）
    let isAdminUser = false;
    if (Auth.isLoggedIn()) {
        try {
            const me = await AuthApi.me();
            if (me && ADMIN_EMAIL_WHITELIST.includes((me.email || '').toLowerCase())) {
                isAdminUser = true;
            }
        } catch (err) {
            // トークン切れ等は無視してボタンを出さないだけにする
        }
    }

    if (!adminPanelToken && !isAdminUser) {
        btn.style.display = 'none';
        return;
    }

    btn.style.display = 'block';
    btn.addEventListener('click', async function () {
        if (!confirm('BOOTHから最新情報を再取得しますか？\n現在のタイトル・価格・説明文が上書きされます。')) return;

        btn.disabled = true;
        btn.textContent = '取得中...';

        try {
            // 経路1のトークンがあればそちらを優先、なければ経路2の理由を使う
            // （/api/admin/* エンドポイントは管理者トークン専用のため、
            //   通常ユーザートークンでは直接呼べない。経路2の場合はサーバー側で
            //   ユーザー権限による別ルートを使う）
            const headers = { 'Content-Type': 'application/json' };

            let endpoint = `${API_BASE_URL}/api/admin/products/${currentProductId}/rescrape`;

            if (adminPanelToken) {
                headers['Authorization'] = `Bearer ${adminPanelToken}`;
            } else {
                // 経路2: 通常のユーザートークンで、ユーザー権限用の再収集エンドポイントを呼ぶ
                endpoint = `${API_BASE_URL}/api/products/${currentProductId}/rescrape`;
                headers['Authorization'] = `Bearer ${Auth.getToken()}`;
            }

            const response = await fetch(endpoint, {
                method: 'POST',
                headers,
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || '再取得に失敗しました');

            showToast('情報を更新しました', 'success');
            location.reload();
        } catch (err) {
            showToast(err.message, 'error');
        } finally {
            btn.disabled = false;
            btn.textContent = '🔄 管理者: 情報を再取得';
        }
    });
}
