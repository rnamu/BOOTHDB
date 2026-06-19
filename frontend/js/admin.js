/* ===========================================
   admin.js - 管理者ページ専用処理
   =========================================== */

'use strict';

const ADMIN_TOKEN_KEY = 'boothdb_admin_token';

// ==========================================
// 管理者トークン管理
// ==========================================

function getAdminToken() {
    return sessionStorage.getItem(ADMIN_TOKEN_KEY);
}

function setAdminToken(token) {
    sessionStorage.setItem(ADMIN_TOKEN_KEY, token);
}

function clearAdminToken() {
    sessionStorage.removeItem(ADMIN_TOKEN_KEY);
}

async function adminFetch(path, options = {}) {
    const token = getAdminToken();
    const headers = {
        'Content-Type': 'application/json',
        ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
        ...(options.headers || {}),
    };
    const response = await fetch(`${API_BASE_URL}${path}`, { ...options, headers });
    if (response.status === 204) return null;
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'エラーが発生しました');
    return data;
}

// ==========================================
// ログイン処理
// ==========================================

async function handleAdminLogin(e) {
    e.preventDefault();
    const password = document.getElementById('admin-password').value;
    const btn = document.getElementById('admin-login-btn');

    btn.disabled = true;
    btn.textContent = 'ログイン中...';

    try {
        const data = await adminFetch('/api/admin/login', {
            method: 'POST',
            body: JSON.stringify({ password }),
        });
        setAdminToken(data.admin_token);
        showDashboard();
    } catch (err) {
        showToast(err.message || 'ログインに失敗しました', 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = 'ログイン';
    }
}

function showDashboard() {
    document.getElementById('admin-login-screen').style.display = 'none';
    document.getElementById('admin-dashboard').style.display = 'block';
    loadAdminProducts();
}

// ==========================================
// タブ切替
// ==========================================

function initTabs() {
    document.querySelectorAll('.admin-tab').forEach(function (tab) {
        tab.addEventListener('click', function () {
            document.querySelectorAll('.admin-tab').forEach(function (t) { t.classList.remove('active'); });
            document.querySelectorAll('.admin-panel').forEach(function (p) { p.classList.remove('active'); });

            tab.classList.add('active');
            const panelId = 'panel-' + tab.getAttribute('data-tab');
            document.getElementById(panelId).classList.add('active');

            // タブに応じてデータを読み込む
            const tabName = tab.getAttribute('data-tab');
            if (tabName === 'products') loadAdminProducts();
            if (tabName === 'reviews')  loadAdminReviews();
            if (tabName === 'users')    loadAdminUsers();
            if (tabName === 'avatars')  loadAdminAvatars();
            if (tabName === 'crawl')    loadCrawlStatus();
        });
    });
}

// ==========================================
// 商品管理
// ==========================================

async function loadAdminProducts() {
    const tbody = document.getElementById('products-tbody');
    tbody.innerHTML = '<tr><td colspan="5" class="loading-spinner">読み込み中...</td></tr>';

    try {
        const data = await adminFetch('/api/admin/products');
        if (!data.items || data.items.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" class="empty-message">商品がありません</td></tr>';
            return;
        }

        tbody.innerHTML = data.items.map(function (p) {
            const date = new Date(p.registered_at).toLocaleDateString('ja-JP');
            const price = p.current_price != null ? `¥${p.current_price.toLocaleString()}` : '-';
            return `
                <tr>
                    <td class="td-title">${escapeHtml(p.title)}</td>
                    <td>${escapeHtml(p.creator_name || '-')}</td>
                    <td class="td-price">${price}</td>
                    <td class="td-date">${date}</td>
                    <td>
                        <div class="admin-action-btns">
                            <a href="detail.html?id=${escapeHtml(p.id)}" target="_blank" class="btn-edit">詳細</a>
                            <button class="btn-edit" onclick="rescrapeProduct('${escapeHtml(p.id)}', '${escapeHtml(p.title)}')">🔄 再収集</button>
                            <button class="btn-delete" onclick="deleteProduct('${escapeHtml(p.id)}', '${escapeHtml(p.title)}')">削除</button>
                        </div>
                    </td>
                </tr>
            `;
        }).join('');
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="5" class="error-message">${escapeHtml(err.message)}</td></tr>`;
    }
}

async function rescrapeProduct(id, title) {
    if (!confirm(`「${title}」の情報をBOOTHから再取得しますか？\nタイトル・価格・説明文が上書きされます。`)) return;
    try {
        showToast('再取得中...', 'info');
        await adminFetch(`/api/admin/products/${id}/rescrape`, { method: 'POST' });
        showToast('情報を更新しました', 'success');
        loadAdminProducts();
    } catch (err) {
        showToast(err.message, 'error');
    }
}

async function deleteProduct(id, title) {
    if (!confirm(`「${title}」を削除しますか？\n価格履歴・レビューも一緒に削除されます。`)) return;
    try {
        await adminFetch(`/api/admin/products/${id}`, { method: 'DELETE' });
        showToast('削除しました', 'success');
        loadAdminProducts();
    } catch (err) {
        showToast(err.message, 'error');
    }
}

// ==========================================
// レビュー管理
// ==========================================

async function loadAdminReviews() {
    const tbody = document.getElementById('reviews-tbody');
    tbody.innerHTML = '<tr><td colspan="6" class="loading-spinner">読み込み中...</td></tr>';

    try {
        const data = await adminFetch('/api/admin/reviews');
        if (!data.items || data.items.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" class="empty-message">レビューがありません</td></tr>';
            return;
        }

        tbody.innerHTML = data.items.map(function (r) {
            const date  = new Date(r.created_at).toLocaleDateString('ja-JP');
            const stars = '★'.repeat(r.rating) + '☆'.repeat(5 - r.rating);
            const title = (r.products || {}).title || '-';
            const username = (r.profiles || {}).username || '匿名';
            return `
                <tr>
                    <td class="td-title">${escapeHtml(title)}</td>
                    <td>${escapeHtml(username)}</td>
                    <td><span class="stars-display">${stars}</span></td>
                    <td class="td-comment">${escapeHtml(r.comment || '-')}</td>
                    <td class="td-date">${date}</td>
                    <td>
                        <div class="admin-action-btns">
                            <button class="btn-delete" onclick="deleteReview('${escapeHtml(r.id)}')">削除</button>
                        </div>
                    </td>
                </tr>
            `;
        }).join('');
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="6" class="error-message">${escapeHtml(err.message)}</td></tr>`;
    }
}

async function deleteReview(id) {
    if (!confirm('このレビューを削除しますか？')) return;
    try {
        await adminFetch(`/api/admin/reviews/${id}`, { method: 'DELETE' });
        showToast('削除しました', 'success');
        loadAdminReviews();
    } catch (err) {
        showToast(err.message, 'error');
    }
}

// ==========================================
// ユーザー管理
// ==========================================

async function loadAdminUsers() {
    const tbody = document.getElementById('users-tbody');
    tbody.innerHTML = '<tr><td colspan="3" class="loading-spinner">読み込み中...</td></tr>';

    try {
        const data = await adminFetch('/api/admin/users');
        if (!data.items || data.items.length === 0) {
            tbody.innerHTML = '<tr><td colspan="3" class="empty-message">ユーザーがいません</td></tr>';
            return;
        }

        tbody.innerHTML = data.items.map(function (u) {
            const date = new Date(u.created_at).toLocaleDateString('ja-JP');
            return `
                <tr>
                    <td style="font-weight: 800;">${escapeHtml(u.username)}</td>
                    <td class="td-date">${date}</td>
                    <td>
                        <div class="admin-action-btns">
                            <button class="btn-delete" onclick="banUser('${escapeHtml(u.id)}', '${escapeHtml(u.username)}')">BAN</button>
                        </div>
                    </td>
                </tr>
            `;
        }).join('');
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="3" class="error-message">${escapeHtml(err.message)}</td></tr>`;
    }
}

async function banUser(id, username) {
    if (!confirm(`「${username}」をBANしますか？\nこの操作は取り消せません。`)) return;
    try {
        await adminFetch(`/api/admin/users/${id}`, { method: 'DELETE' });
        showToast(`${username} をBANしました`, 'success');
        loadAdminUsers();
    } catch (err) {
        showToast(err.message, 'error');
    }
}

// ==========================================
// アバター管理
// ==========================================

async function loadAdminAvatars() {
    const tbody = document.getElementById('avatars-tbody');
    tbody.innerHTML = '<tr><td colspan="4" class="loading-spinner">読み込み中...</td></tr>';

    try {
        const data = await adminFetch('/api/avatars');
        if (!data.items || data.items.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4" class="empty-message">アバターがありません</td></tr>';
            return;
        }

        tbody.innerHTML = data.items.map(function (a) {
            return `
                <tr>
                    <td style="font-weight: 800;">${escapeHtml(a.name)}</td>
                    <td>${escapeHtml(a.name_en || '-')}</td>
                    <td>${escapeHtml(a.creator || '-')}</td>
                    <td>
                        <div class="admin-action-btns">
                            <button class="btn-edit" onclick="openEditAvatar('${escapeHtml(a.id)}', '${escapeHtml(a.name)}', '${escapeHtml(a.name_en || '')}', '${escapeHtml(a.creator || '')}')">編集</button>
                            <button class="btn-delete" onclick="deleteAvatar('${escapeHtml(a.id)}', '${escapeHtml(a.name)}')">削除</button>
                        </div>
                    </td>
                </tr>
            `;
        }).join('');
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="4" class="error-message">${escapeHtml(err.message)}</td></tr>`;
    }
}

function openAddAvatar() {
    document.getElementById('avatar-modal-title').textContent = 'アバターを追加';
    document.getElementById('avatar-edit-id').value = '';
    document.getElementById('avatar-name-input').value = '';
    document.getElementById('avatar-name-en-input').value = '';
    document.getElementById('avatar-creator-input').value = '';
    document.getElementById('avatar-submit-btn').textContent = '追加する';
    openModal('avatar-modal');
}

function openEditAvatar(id, name, nameEn, creator) {
    document.getElementById('avatar-modal-title').textContent = 'アバターを編集';
    document.getElementById('avatar-edit-id').value = id;
    document.getElementById('avatar-name-input').value = name;
    document.getElementById('avatar-name-en-input').value = nameEn;
    document.getElementById('avatar-creator-input').value = creator;
    document.getElementById('avatar-submit-btn').textContent = '更新する';
    openModal('avatar-modal');
}

async function handleAvatarSubmit(e) {
    e.preventDefault();
    const id      = document.getElementById('avatar-edit-id').value;
    const name    = document.getElementById('avatar-name-input').value.trim();
    const nameEn  = document.getElementById('avatar-name-en-input').value.trim();
    const creator = document.getElementById('avatar-creator-input').value.trim();
    const btn     = document.getElementById('avatar-submit-btn');

    btn.disabled = true;

    try {
        if (id) {
            await adminFetch(`/api/admin/avatars/${id}`, {
                method: 'PATCH',
                body: JSON.stringify({ name, name_en: nameEn, creator }),
            });
            showToast('更新しました', 'success');
        } else {
            await adminFetch('/api/admin/avatars', {
                method: 'POST',
                body: JSON.stringify({ name, name_en: nameEn, creator }),
            });
            showToast('追加しました', 'success');
        }
        closeModal('avatar-modal');
        loadAdminAvatars();
    } catch (err) {
        showToast(err.message, 'error');
    } finally {
        btn.disabled = false;
    }
}

async function deleteAvatar(id, name) {
    if (!confirm(`「${name}」を削除しますか？`)) return;
    try {
        await adminFetch(`/api/admin/avatars/${id}`, { method: 'DELETE' });
        showToast('削除しました', 'success');
        loadAdminAvatars();
    } catch (err) {
        showToast(err.message, 'error');
    }
}

// ==========================================
// 自動収集（クロール）管理
// ==========================================

async function loadCrawlStatus() {
    const tbody = document.getElementById('crawl-tbody');
    const badge = document.getElementById('crawl-running-badge');
    tbody.innerHTML = '<tr><td colspan="4" class="loading-spinner">読み込み中...</td></tr>';

    try {
        const data = await adminFetch('/api/admin/crawl/status');

        badge.style.display = data.running ? 'block' : 'none';

        if (!data.categories || data.categories.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4" class="empty-message">まだ収集履歴がありません</td></tr>';
            return;
        }

        tbody.innerHTML = data.categories.map(function (c) {
            const date = c.updated_at ? new Date(c.updated_at).toLocaleString('ja-JP') : '-';
            return `
                <tr>
                    <td style="font-weight: 800;">${escapeHtml(c.category)}</td>
                    <td>${c.last_page ?? 0} ページ目</td>
                    <td>${(c.total_collected ?? 0).toLocaleString()} 件</td>
                    <td class="td-date">${date}</td>
                </tr>
            `;
        }).join('');
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="4" class="error-message">${escapeHtml(err.message)}</td></tr>`;
    }
}

async function runCrawlNow() {
    const btn = document.getElementById('run-crawl-btn');
    if (!confirm('カテゴリの自動収集を今すぐ実行しますか？\n完了まで数分かかります（バックグラウンドで進みます）。')) return;

    btn.disabled = true;
    btn.textContent = '実行を開始中...';

    try {
        const data = await adminFetch('/api/admin/crawl/run', { method: 'POST' });
        showToast(data.message || 'クロールを開始しました', 'success');
        // 進捗を定期的に確認
        setTimeout(loadCrawlStatus, 3000);
    } catch (err) {
        showToast(err.message, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = '▶ 今すぐ収集を実行';
    }
}

// ==========================================
// 初期化
// ==========================================

document.addEventListener('DOMContentLoaded', function () {
    // すでにトークンがあればダッシュボードを表示
    if (getAdminToken()) {
        showDashboard();
    }

    // ログインフォーム
    const loginForm = document.getElementById('admin-login-form');
    if (loginForm) loginForm.addEventListener('submit', handleAdminLogin);

    // タブ初期化
    initTabs();

    // アバター追加ボタン
    const addBtn = document.getElementById('open-add-avatar-btn');
    if (addBtn) addBtn.addEventListener('click', openAddAvatar);

    // アバターフォーム
    const avatarForm = document.getElementById('avatar-form');
    if (avatarForm) avatarForm.addEventListener('submit', handleAvatarSubmit);

    // クロール実行ボタン
    const runCrawlBtn = document.getElementById('run-crawl-btn');
    if (runCrawlBtn) runCrawlBtn.addEventListener('click', runCrawlNow);
});
