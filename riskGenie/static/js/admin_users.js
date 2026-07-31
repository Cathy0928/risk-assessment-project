(function () {
    "use strict";

    var state = {
        roles: [],
        users: [],
        editingUserId: null
    };

    var elements = {};

    document.addEventListener("DOMContentLoaded", function () {
        elements = {
            createForm: document.getElementById("create-user-form"),
            createRole: document.getElementById("create-role"),
            createSubmit: document.getElementById("create-user-submit"),
            refreshButton: document.getElementById("refresh-users"),
            message: document.getElementById("page-message"),
            loading: document.getElementById("users-loading"),
            tableContainer: document.getElementById("users-table-container"),
            usersBody: document.getElementById("users-body"),
            empty: document.getElementById("users-empty"),
            count: document.getElementById("user-count")
        };

        elements.createForm.addEventListener("submit", handleCreate);
        elements.refreshButton.addEventListener("click", function () {
            loadPageData(true);
        });

        loadPageData(false);
    });

    async function loadPageData(showConfirmation) {
        setLoading(true);
        clearMessage();

        try {
            var responses = await Promise.all([
                apiRequest("/api/admin/roles"),
                apiRequest("/api/admin/users")
            ]);

            state.roles = Array.isArray(responses[0].roles) ? responses[0].roles : [];
            state.users = Array.isArray(responses[1].users) ? responses[1].users : [];
            state.editingUserId = null;
            renderRoleOptions();
            renderUsers();

            if (showConfirmation) {
                showMessage("資料已重新整理。", "success");
            }
        } catch (error) {
            showMessage(getErrorMessage(error), "error");
        } finally {
            setLoading(false);
        }
    }

    async function handleCreate(event) {
        event.preventDefault();

        var formData = new FormData(elements.createForm);
        var payload = {
            username: String(formData.get("username") || "").trim(),
            email: String(formData.get("email") || "").trim(),
            password: String(formData.get("password") || ""),
            role_id: String(formData.get("role_id") || "").trim()
        };

        setCreateBusy(true);
        clearMessage();

        try {
            var response = await apiRequest("/api/admin/users", {
                method: "POST",
                body: JSON.stringify(payload)
            });
            state.users.push(response.user);
            elements.createForm.reset();
            renderRoleOptions();
            renderUsers();
            showMessage("帳號已成功新增。", "success");
        } catch (error) {
            showMessage(getErrorMessage(error), "error");
        } finally {
            setCreateBusy(false);
        }
    }

    function renderRoleOptions() {
        var selectedValue = elements.createRole.value;
        elements.createRole.replaceChildren();

        var placeholder = document.createElement("option");
        placeholder.value = "";
        placeholder.textContent = state.roles.length ? "請選擇角色" : "目前沒有角色資料";
        elements.createRole.appendChild(placeholder);

        state.roles.forEach(function (role) {
            var option = document.createElement("option");
            option.value = String(role.id);
            option.textContent = role.role_name || String(role.id);
            elements.createRole.appendChild(option);
        });

        elements.createRole.disabled = state.roles.length === 0;
        if (selectedValue && state.roles.some(function (role) {
            return String(role.id) === selectedValue;
        })) {
            elements.createRole.value = selectedValue;
        }
    }

    function renderUsers() {
        elements.usersBody.replaceChildren();
        elements.count.textContent = state.users.length + " 位使用者";
        elements.empty.hidden = state.users.length !== 0;
        elements.tableContainer.hidden = state.users.length === 0;

        state.users.forEach(function (user) {
            elements.usersBody.appendChild(createUserRow(user));
        });
    }

    function createUserRow(user) {
        var row = document.createElement("tr");
        var isEditing = state.editingUserId === String(user.id);

        if (user.is_active === false) {
            row.classList.add("is-disabled");
        }

        if (isEditing) {
            appendCell(row, createTextInput(user.username || ""));
        } else {
            appendTextCell(row, user.username || "未命名");
        }

        appendTextCell(row, user.email || "未提供");

        if (isEditing) {
            appendCell(row, createRoleSelect(user.role_id));
        } else {
            appendTextCell(row, getRoleName(user.role_id));
        }

        appendCell(row, createStatusBadge(user.is_active));
        appendCell(row, createActions(user, isEditing));
        return row;
    }

    function createTextInput(value) {
        var input = document.createElement("input");
        input.type = "text";
        input.name = "username";
        input.value = value;
        input.required = true;
        input.setAttribute("aria-label", "使用者名稱");
        return input;
    }

    function createRoleSelect(currentRoleId) {
        var select = document.createElement("select");
        select.name = "role_id";
        select.required = true;
        select.setAttribute("aria-label", "角色");

        state.roles.forEach(function (role) {
            var option = document.createElement("option");
            option.value = String(role.id);
            option.textContent = role.role_name || String(role.id);
            option.selected = String(role.id) === String(currentRoleId);
            select.appendChild(option);
        });

        return select;
    }

    function createStatusBadge(isActive) {
        var badge = document.createElement("span");
        badge.classList.add("status-badge");

        if (isActive === true) {
            badge.classList.add("status-active");
            badge.textContent = "啟用";
        } else {
            badge.classList.add("status-disabled");
            badge.textContent = "已停用";
        }

        return badge;
    }

    function createActions(user, isEditing) {
        var container = document.createElement("div");
        container.className = "row-actions";

        if (isEditing) {
            container.appendChild(createActionButton("儲存", function (event) {
                saveUser(user, event.currentTarget.closest("tr"));
            }));
            container.appendChild(createActionButton("取消", function () {
                state.editingUserId = null;
                renderUsers();
            }));
            return container;
        }

        container.appendChild(createActionButton("編輯", function () {
            state.editingUserId = String(user.id);
            renderUsers();
        }));

        var disableButton = createActionButton("停用", function (event) {
            disableUser(user, event.currentTarget.closest("tr"));
        }, "danger");
        disableButton.disabled = user.is_active !== true;
        container.appendChild(disableButton);
        return container;
    }

    function createActionButton(label, handler, extraClass) {
        var button = document.createElement("button");
        button.type = "button";
        button.className = "table-action" + (extraClass ? " " + extraClass : "");
        button.textContent = label;
        button.addEventListener("click", handler);
        return button;
    }

    async function saveUser(user, row) {
        var username = row.querySelector('[name="username"]').value.trim();
        var roleId = row.querySelector('[name="role_id"]').value;

        if (!username) {
            showMessage("使用者名稱不可空白。", "error");
            return;
        }
        if (!roleId) {
            showMessage("請選擇角色。", "error");
            return;
        }

        setRowBusy(row, true);
        clearMessage();

        try {
            var response = await apiRequest("/api/admin/users/" + encodeURIComponent(user.id), {
                method: "PATCH",
                body: JSON.stringify({
                    username: username,
                    role_id: roleId
                })
            });
            mergeUser(user.id, response.user);
            state.editingUserId = null;
            renderUsers();
            showMessage("帳號資料已更新。", "success");
        } catch (error) {
            showMessage(getErrorMessage(error), "error");
            setRowBusy(row, false);
        }
    }

    async function disableUser(user, row) {
        var displayName = user.username || user.email || "此帳號";
        if (!window.confirm("確定要停用「" + displayName + "」嗎？")) {
            return;
        }

        setRowBusy(row, true);
        clearMessage();

        try {
            var response = await apiRequest(
                "/api/admin/users/" + encodeURIComponent(user.id) + "/disable",
                {method: "POST"}
            );
            mergeUser(user.id, Object.assign({}, response.user || {}, {is_active: false}));
            renderUsers();
            showMessage(
                response.already_disabled ? "此帳號原先已停用。" : "帳號已停用。",
                "success"
            );
        } catch (error) {
            showMessage(getErrorMessage(error), "error");
            setRowBusy(row, false);
        }
    }

    function mergeUser(userId, updatedUser) {
        state.users = state.users.map(function (user) {
            if (String(user.id) !== String(userId)) {
                return user;
            }
            return Object.assign({}, user, updatedUser || {});
        });
    }

    function getRoleName(roleId) {
        var role = state.roles.find(function (item) {
            return String(item.id) === String(roleId);
        });
        return role ? role.role_name : (roleId || "未提供");
    }

    function appendTextCell(row, text) {
        var cell = document.createElement("td");
        cell.textContent = text;
        row.appendChild(cell);
    }

    function appendCell(row, child) {
        var cell = document.createElement("td");
        cell.appendChild(child);
        row.appendChild(cell);
    }

    function setLoading(isLoading) {
        elements.loading.hidden = !isLoading;
        elements.refreshButton.disabled = isLoading;
        if (isLoading) {
            elements.tableContainer.hidden = true;
            elements.empty.hidden = true;
        } else if (state.users.length) {
            elements.tableContainer.hidden = false;
        } else {
            elements.empty.hidden = false;
        }
    }

    function setCreateBusy(isBusy) {
        elements.createSubmit.disabled = isBusy;
        elements.createSubmit.textContent = isBusy ? "新增中" : "新增帳號";
    }

    function setRowBusy(row, isBusy) {
        row.querySelectorAll("button, input, select").forEach(function (control) {
            control.disabled = isBusy;
        });
    }

    function showMessage(message, type) {
        elements.message.textContent = message;
        elements.message.className = "page-message " + type;
        elements.message.hidden = false;
    }

    function clearMessage() {
        elements.message.hidden = true;
        elements.message.textContent = "";
        elements.message.className = "page-message";
    }

    async function apiRequest(url, options) {
        var config = Object.assign({
            method: "GET",
            credentials: "same-origin",
            headers: {
                "Accept": "application/json"
            }
        }, options || {});

        if (config.body) {
            config.headers["Content-Type"] = "application/json";
        }

        var response = await fetch(url, config);
        var data = null;

        try {
            data = await response.json();
        } catch (error) {
            data = null;
        }

        if (!response.ok) {
            var requestError = new Error("Request failed");
            requestError.status = response.status;
            requestError.payload = data;
            throw requestError;
        }

        return data || {};
    }

    function getErrorMessage(error) {
        var payload = error.payload || {};

        if (error.status === 400) {
            return payload.message || payload.error || "輸入資料有誤，請檢查後再試。";
        }
        if (error.status === 401) {
            return "登入狀態已失效，請重新登入。";
        }
        if (error.status === 403) {
            return "您沒有執行此操作的權限。";
        }
        if (error.status === 404) {
            return "找不到指定的使用者。";
        }
        if (error.status === 409) {
            return "此電子郵件已被使用。";
        }
        if (error.status >= 500) {
            return "伺服器暫時無法處理，請稍後再試。";
        }
        return "操作失敗，請稍後再試。";
    }
}());
