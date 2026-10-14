/* 登录门禁。**在 app.js 之前加载**：门先于内容。
 *
 * 三件事：
 *   ① 开页面先问 GET /api/auth/me，由它决定显示哪一屏——不是先显示再挡住；
 *   ② 登录 / 注册；
 *   ③ 登出：让服务端删掉那一行会话，再把浏览器里属于上一个人的东西清干净。
 *
 * **为什么要包一层 IIFE**：app.js 是**脚本顶层声明**（没有包装），
 * 它已经在全局词法环境里占了 landingPage、resultPage 这些名字。
 * 这里再写一遍同名的 const 会直接 "Identifier has already been declared" 整份脚本不执行。
 *
 * 它反过来要调 app.js 的 clearSession()：顶层 function 声明会挂到全局，
 * 按名字直接取即可（缺失时下面的 typeof 会挡住）。
 *
 * 设计见 docs/ASK_LONGTERM_MEMORY.md §2.1。
 */
(function () {
    'use strict';

    var loginPage = document.getElementById('login-page');
    var landingPage = document.getElementById('landing-page');
    var form = document.getElementById('login-form');
    var usernameInput = document.getElementById('login-username');
    var passwordInput = document.getElementById('login-password');
    var errorBox = document.getElementById('login-error');
    var loginButton = document.getElementById('login-submit');
    var modeTitle = document.getElementById('login-mode-title');
    var modeHint = document.getElementById('login-mode-hint');
    var switchHint = document.getElementById('login-switch-hint');
    var switchButton = document.getElementById('login-switch');
    var toggleButton = document.getElementById('login-password-toggle');

    // 页面是旧的（缓存的 HTML 没这几个节点）却加载了新 JS：安静退出，
    // 不要在这里抛错把 app.js 也连累掉。
    if (!loginPage || !form || !landingPage || !switchButton) {
        return;
    }

    function showError(message) {
        errorBox.textContent = message;
        errorBox.classList.remove('hidden');
    }

    function clearError() {
        errorBox.textContent = '';
        errorBox.classList.add('hidden');
    }

    /* 两屏共用同一个表单，靠这张表切换。
     *
     * 一开始是"并排两个按钮"（登录 + 注册新账号），但那样屏上永远长一个样：
     * 用户点完「注册新账号」不知道自己进没进注册界面。所以改成**上下一屏**——
     * 标题、说明、主按钮、切换链接全跟着变，当前在哪一屏一眼可见。 */
    var MODES = {
        login: {
            title: '登录',
            // 登录屏**不给**说明：站在这一屏的人本来就有账号，"已有账号？…"是废话。
            // 注册屏才需要（那里要交代用户名和密码的规矩）。
            hint: '',
            submit: '登录',
            path: '/api/auth/login',
            empty: '登录要填用户名和密码',
            switchHint: '还没有账号？',
            switchLabel: '注册一个',
            next: 'register',
        },
        register: {
            title: '注册新账号',
            hint: '用户名 2-32 个字符、不含空格；密码至少 6 位。',
            submit: '注册',
            path: '/api/auth/register',
            empty: '注册要先填用户名和密码',
            switchHint: '已经有账号了？',
            switchLabel: '去登录',
            next: 'login',
        },
    };

    var mode = 'login';

    function applyMode(next) {
        mode = next;
        var spec = MODES[mode];
        modeTitle.textContent = spec.title;
        modeHint.textContent = spec.hint;
        // 说明为空时**整行收起**：留着的话 margin 还在，标题和第一个字段之间会多出一道空白。
        modeHint.classList.toggle('hidden', !spec.hint);
        loginButton.textContent = spec.submit;
        switchHint.textContent = spec.switchHint;
        switchButton.textContent = spec.switchLabel;
        // 换屏了：上一条错误说的是另一件事（"注册要先填…"），留着会误导。
        // 已经敲进去的内容**不清**——用户很可能就是填到一半才发现要注册的。
        clearError();
        // 也收回眼睛：换屏之后还把密码亮着，等于把它晾在那儿给旁边人看。
        revealPassword(false);
        usernameInput.focus();
    }

    /* 小眼睛。密码打错了只能整条重打，这是登录页最常见的一次挫败。
     *
     * 切换 type 会让光标跳到开头（浏览器重建了那个控件），所以要**手动把光标放回末尾**，
     * 否则用户点完眼睛再接着打字，字会插到最前面去。 */
    function revealPassword(show) {
        passwordInput.type = show ? 'text' : 'password';
        toggleButton.classList.toggle('is-revealed', show);
        toggleButton.setAttribute('aria-pressed', show ? 'true' : 'false');
        toggleButton.setAttribute('aria-label', show ? '隐藏密码' : '显示密码');
    }

    function togglePassword() {
        var show = passwordInput.type === 'password';
        revealPassword(show);
        if (document.activeElement === toggleButton || show) {
            var end = passwordInput.value.length;
            passwordInput.focus();
            try {
                passwordInput.setSelectionRange(end, end);
            } catch (error) {
                // 某些浏览器不允许给 password 类型设选区。光标位置是锦上添花，
                // 设不上就算了，不能让整个切换挂掉。
            }
        }
    }

    /* 把服务端的错误信封翻成一句人话。
     * 信封是 ApiErrorResponse：{code, message, details}。
     * message 是概括（"请求参数校验失败"），真正有用的是 details（"password: 密码至少 6 位"）。 */
    function errorText(payload) {
        if (!payload || typeof payload !== 'object') {
            return '登录失败，请重试';
        }
        var details = Array.isArray(payload.details) ? payload.details : [];
        if (details.length) {
            return details.join('；');
        }
        return payload.message || '登录失败，请重试';
    }

    function readJson(response) {
        // 500 那条路回的可能不是 JSON。解析失败不该盖掉"请求本身失败了"这件事。
        return response.json().catch(function () { return null; });
    }

    function setBusy(busy) {
        loginButton.disabled = busy;
        switchButton.disabled = busy;
        // 恢复的是**当前这一屏**的主按钮文案（登录 / 注册），不是写死的「登录」
        loginButton.textContent = busy ? '请稍候…' : MODES[mode].submit;
    }

    /* 认过人之后：把用户名写进页头两处，露出规划页。
     *
     * 这里**不碰 sessionStorage**——这一屏该显示落地页还是上一趟的方案，
     * 是 app.js 的 restoreSession() 决定的，它在门禁这个 Promise 之后才跑。 */
    function enterApp(user) {
        loginPage.classList.add('hidden');
        landingPage.classList.remove('hidden');
        var name = user && user.username ? user.username : '';
        document.querySelectorAll('.account-name').forEach(function (node) {
            node.textContent = name;
        });
    }

    function showLogin() {
        landingPage.classList.add('hidden');
        var resultPage = document.getElementById('result-page');
        if (resultPage) {
            resultPage.classList.add('hidden');
        }
        // 每次都从登录屏开始：登出之后再进来，落在"注册"上会很奇怪。
        applyMode('login');
        loginPage.classList.remove('hidden');
        usernameInput.focus();
    }

    /* 门禁本体。返回值就是"这个人认出来了没有"，app.js 的入口要拿它做判断——
     * 没认出来还去 restoreSession() 的话，上一个人的方案会被渲染到登录页底下。 */
    async function boot() {
        var user = null;
        try {
            var response = await fetch('/api/auth/me', {
                headers: { Accept: 'application/json' },
            });
            if (response.ok) {
                user = await readJson(response);
            }
        } catch (error) {
            // 服务连不上：当作没登录。让用户落在登录页（那里有报错位可以写），
            // 而不是两个 section 都 hidden 的空屏。
        }
        if (user && user.username) {
            enterApp(user);
            return true;
        }
        showLogin();
        return false;
    }

    async function submit() {
        var spec = MODES[mode];
        // 只拦"压根没填"，长度/字节/空格那些判据归服务端——在前端再抄一遍，
        // 两处迟早会不一致，而不一致的那天用户会看到两种说法。
        //
        // 提示文案**分屏**（spec.empty）：两屏共用同一组输入框，只回一句
        // 「用户名和密码都要填」的话，正在注册的人会以为这里只能登录。
        // 顺带把光标放到缺的那个框里。
        if (!usernameInput.value.trim() || !passwordInput.value) {
            showError(spec.empty);
            (usernameInput.value.trim() ? passwordInput : usernameInput).focus();
            return;
        }
        clearError();
        setBusy(true);
        try {
            var response = await fetch(spec.path, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    username: usernameInput.value.trim(),
                    password: passwordInput.value,
                }),
            });
            var payload = await readJson(response);
            if (!response.ok) {
                showError(errorText(payload));
                return;
            }
            // 换人了。浏览器里留着的是**上一个人**的方案和对话，先清掉再进去
            // （docs/ASK_LONGTERM_MEMORY.md §一 第 8 条）。
            if (typeof clearSession === 'function') {
                clearSession();
            }
            // 整页重来而不是就地切屏：上一个人的 DOM（方案、气泡）还挂着，
            // 一块块擦容易漏，而漏掉的那块恰恰是最难发现的。
            window.location.reload();
        } catch (error) {
            showError('连不上服务，请稍后重试');
        } finally {
            setBusy(false);
        }
    }

    async function logout() {
        try {
            await fetch('/api/auth/logout', { method: 'POST' });
        } catch (error) {
            // 网断了也照样往下走：**浏览器这一侧必须清掉**，
            // 否则下一个人打开看到的是上一个人的方案。
        }
        if (typeof clearSession === 'function') {
            clearSession();
        }
        window.location.reload();
    }

    form.addEventListener('submit', function (event) {
        // 回车走**当前这一屏**的主操作，而不是永远登录
        event.preventDefault();
        submit();
    });

    switchButton.addEventListener('click', function () {
        applyMode(MODES[mode].next);
    });

    if (toggleButton) {
        toggleButton.addEventListener('click', togglePassword);
    }

    document.querySelectorAll('.logout-btn').forEach(function (button) {
        button.addEventListener('click', logout);
    });

    // app.js 的入口在等它（见 app.js 末尾）。先赋上再跑，避免那一侧读到 undefined。
    window.travelAuthReady = boot();
})();
