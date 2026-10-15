    const form = document.getElementById('trip-form');
    const landingPage = document.getElementById('landing-page');
    const resultPage = document.getElementById('result-page');
    const statusText = document.getElementById('status');
    const errorBox = document.getElementById('error-box');
    const errorToast = document.getElementById('error-toast');
    const submitBtn = document.getElementById('submit-btn');
    const progressPanel = document.getElementById('progress-panel');
    const progressFill = document.getElementById('progress-fill');
    const progressLabel = document.getElementById('progress-label');
    const progressStage = document.getElementById('progress-stage');
    const progressPercent = document.getElementById('progress-percent');
    const backHomeBtn = document.getElementById('back-home-btn');
    const editTripBtn = document.getElementById('edit-trip-btn');
    // 旅行助手。它是 .result-layout 的第二列，与 main.dashboard-main 互斥显示——
    // 左栏（aside.side-nav）全程在，切换只换右半边。见 docs/ASSISTANT_CHAT_DESIGN.md §八。
    const assistantPanel = document.getElementById('assistant-panel');
    const assistantEntry = document.getElementById('open-assistant');
    const dashboardMain = resultPage.querySelector('.dashboard-main');
    const assistantMessages = document.getElementById('assistant-messages');
    const assistantEmpty = document.getElementById('assistant-empty');
    const tripAdjustOverlay = document.getElementById('trip-adjust-overlay');
    const preferenceGroup = document.getElementById('preference-group');
    const hiddenPreferences = document.getElementById('preferences');
    const departureDateInput = document.getElementById('departureDate');
    const endDateInput = document.getElementById('endDate');
    const daysDisplay = document.getElementById('daysDisplay');
    const navItems = document.querySelectorAll('.nav-item[data-target]');
    const topActions = resultPage.querySelector('.top-actions');
    const askQuestion = document.getElementById('ask-question');

    let progressTimer = null;
    let currentTripData = null;
    let currentPayload = null;
    // 短期记忆：这趟会话里前几轮的问答，每轮 {role, content}。
    // 与 currentTripData / currentPayload 一起进 sessionStorage，理由见
    // docs/ASK_MULTITURN_MEMORY.md §3.1。关标签页就没了，与方案同生共死。
    let chatHistory = [];
    let mapConfigCache = null;
    let amapSdkPromise = null;
    let amapMap = null;
    let amapMarkers = [];
    let amapPolyline = null;
    let amapInfoWindow = null;
    let currentMapDayIndex = 0;

    // 天数翻牌用（反馈单辅助创意 B）。这两个声明必须落在下方 initializeDefaultDates /
    // syncDateRange 的调用之前 —— const 在声明前处于暂时性死区，放函数定义旁边会直接报错。
    const reducedMotionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
    let daysFlipTimer = null;

    // 偏好也进票头摘要句（SLIM 修复 3）：这是本单唯一新增的事件绑定，纯展示层。
    // updateLandingPreview 是函数声明，此时已可调用；实际触发在用户点选之后。
    preferenceGroup.addEventListener('change', () => {
        syncPreferenceValues();
        updateLandingPreview();
    });
    departureDateInput.addEventListener('change', syncDateRange);
    endDateInput.addEventListener('change', syncDateRange);
    backHomeBtn.addEventListener('click', () => {
        // 回首页前把右半边还原成方案视图：不然下次进来还停在助手页上，
        // 而那时 currentTripData 可能已经被换掉了。is-assistant 那道锁也得摘，
        // 否则整个 #result-page 还是 100vh + overflow:hidden。
        showPlanView();
        resultPage.classList.add('hidden');
        landingPage.classList.remove('hidden');
        window.scrollTo({ top: 0, behavior: 'smooth' });
    });
    editTripBtn.addEventListener('click', adjustCurrentTrip);
    // 侧栏那个「旅行助手」不是页内一节，是换右半边——所以它没有 data-target，不进 navItems。
    assistantEntry.addEventListener('click', openAssistant);
    // 窄屏专用（宽屏上它 display:none，点不到）
    document.getElementById('assistant-back-btn').addEventListener('click', closeAssistant);

    // Enter 发送，Shift+Enter 换行——聊天框的通行做法，不然每次都要把手挪到鼠标上。
    askQuestion.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter' || event.shiftKey) {
            return;
        }
        // **组字期间必须放行**：中文输入法用 Enter 上屏，那一下是"确认候选词"，
        // 不是"发送"。拦了的话打完拼音一按回车就把半截问题发出去了。
        // isComposing 是标准信号，keyCode 229 是部分 IME / 老浏览器的老写法，两个都看。
        if (event.isComposing || event.keyCode === 229) {
            return;
        }
        event.preventDefault();
        if (!askQuestion.value.trim()) {
            return;   // 空着按 Enter 什么也不做，不弹提示（手滑比手快常见）
        }
        askAboutCurrentTrip();
    });

    // 空态那几个示例问题。**事件委托到消息区**，不绑在按钮本身上：
    // 空态节点会被 resetAssistant 摘下来再挂回去，绑在它内部就得跟着搬。
    assistantMessages.addEventListener('click', (event) => {
        const sample = event.target.closest('.assistant-sample');
        if (!sample) {
            return;
        }
        askQuestion.value = sample.textContent.trim();
        askAboutCurrentTrip();
    });
    // 滚动联动高亮。**原来只在点击时切**，所以往下滑的时候左边一直停在原地。
    //
    // 位置用"文档绝对坐标"（累加 offsetTop）而不是 `getBoundingClientRect().top`：
    // 「景点地图」那张卡在 `.right-rail` 里是 `position: sticky`，rect.top 会被钉在视口上沿，
    // 一滚进内容区它就永远满足"在激活线上方"。绝对坐标是它**没被钉住时**的位置，稳定。
    let navClickLocked = false;
    let navUnlockTimer = null;
    const NAV_ACTIVE_LINE = 140;   // 视口上沿往下这么多像素，算"这一节已经开始了"

    // 点击跳转是一次平滑滚动，中途会路过好几个节。不锁住的话高亮会被路过的抢走，
    // 最后停在哪一节反而看不出来——所以锁到滚动停下来为止。
    function holdNavDuringClick() {
        navClickLocked = true;
        clearTimeout(navUnlockTimer);
        navUnlockTimer = setTimeout(() => { navClickLocked = false; }, 150);
    }

    function documentTop(element) {
        let top = 0;
        for (let node = element; node; node = node.offsetParent) {
            top += node.offsetTop;
        }
        return top;
    }

    function syncNavToScroll() {
        const spots = [];
        navItems.forEach(item => {
            const section = document.getElementById(item.dataset.target);
            // 右边栏那张卡全程钉在视口里，"滚到哪一节"对它没有意义，所以不参与自动高亮。
            // 点它照样能跳能亮——那条路走 setActiveNav。
            if (!section || section.closest('.right-rail')) return;
            spots.push({ id: item.dataset.target, top: documentTop(section) });
        });
        if (!spots.length) return;
        // 按**文档顺序**排，不是导航里的顺序——这两个不一样（「景点地图」在导航里排第三，
        // 但它在右边栏，文档里排在天气之后）。照导航顺序取"最后一个过线的"会指错节。
        spots.sort((first, second) => first.top - second.top);
        // 滚到底就直接认最后一节。「方案追问」还在这一页时，这条是专为它写的（它在文档最末，
        // 够不到激活线）。它走之后最后一节变成「每日行程」——它是长是短取决于行程天数，
        // 短行程同样够不到线，所以这条留着。
        if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4) {
            setActiveNav(spots[spots.length - 1].id);
            return;
        }
        const line = window.scrollY + NAV_ACTIVE_LINE;
        let current = spots[0].id;
        spots.forEach(spot => {
            if (spot.top <= line) current = spot.id;
        });
        setActiveNav(current);
    }

    navItems.forEach(item => {
        item.addEventListener('click', () => {
            // 在助手页点导航项 = "我要回去看那一节"。得先切回方案视图再滚，
            // 否则滚的是被 .hidden 藏起来的节点——那些节点没有布局盒，滚了等于没反应。
            showPlanView();
            setActiveNav(item.dataset.target);
            holdNavDuringClick();
            document.getElementById(item.dataset.target)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
        });
    });
    // 顶栏那三个按钮是常驻的（`返回首页` 是离开结果页的唯一出口，
    // 页面四五屏长，滚下去就找不着了）。底色只在真的滚起来之后才加——
    // 一进页面挂在蓝天顶上会显出一条浅横带。见 result.css 的 .is-stuck。
    function syncTopActionsBacking() {
        topActions.classList.toggle('is-stuck', window.scrollY > 8);
    }

    window.addEventListener('scroll', () => {
        syncTopActionsBacking();
        if (navClickLocked) {
            holdNavDuringClick();   // 还在滚，接着续期
            return;
        }
        syncNavToScroll();
    }, { passive: true });
    document.getElementById('ask-submit').addEventListener('click', askAboutCurrentTrip);
    document.getElementById('ask-confirm-yes').addEventListener('click', confirmAskChange);
    document.getElementById('ask-confirm-no').addEventListener('click', hideAskConfirmation);
    document.getElementById('ask-hotel-move-yes').addEventListener('click', () => chooseHotelMove(true));
    document.getElementById('ask-hotel-move-no').addEventListener('click', () => chooseHotelMove(false));

    form.noValidate = true;

    form.addEventListener('submit', async (event) => {
        event.preventDefault();
        await handleTripSubmit();
    });

    submitBtn.addEventListener('click', async (event) => {
        event.preventDefault();
        await handleTripSubmit();
    });

    async function handleTripSubmit() {
        if (submitBtn.disabled) {
            return;
        }
        syncPreferenceValues();
        syncDateRange();
        clearError();
        const payload = buildTripPayload();
        const validationMessage = validateTripPayload(payload);
        if (validationMessage) {
            showError(validationMessage);
            return;
        }

        setLoading(true);
        startFakeProgress();
        try {
            const response = await fetch('/api/trips/plan', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(payload)
            });

            if (!response.ok) {
                const errorData = await response.json().catch(() => null);
                console.error('Trip plan request failed:', errorData);
                throw new Error(formatErrorMessage(errorData) || '旅行计划生成失败');
            }

            const data = await response.json();
            completeProgress();
            resetAssistant();
            renderResult(data, payload);
            setTimeout(() => {
                landingPage.classList.add('hidden');
                resultPage.classList.remove('hidden');
                window.scrollTo({ top: 0, behavior: 'smooth' });
                // 上一单看到一半留下的高亮不该跨单留着。刻意滚回顶部时不会触发 scroll 事件，
                // 所以这里得主动对齐一次。
                syncNavToScroll();
            }, 300);
        } catch (error) {
            stopProgress();
            showError(error.message);
        } finally {
            setLoading(false);
        }
    }

    initializeDefaultDates();
    syncPreferenceValues();
    syncDateRange();

    // 必须排在上面三行之后：initializeDefaultDates / syncDateRange 才把日期填进输入框，
    // 早于它们调用的话 getTripDays() 读到的是空日期，预览会显示成「1 DAYS」。
    bindLandingPreview();

    // 票头实时预览：只读输入框的值、只写预览区的文本。
    // 不参与提交、不参与校验、不产生任何新状态（FRONTEND_FEEDBACK_LANDING_BOLD 红线）。
    function updateLandingPreview() {
        const originEl = document.getElementById('preview-origin');
        const cityEl = document.getElementById('preview-city');
        const factsEl = document.getElementById('preview-facts');
        if (!originEl || !cityEl || !factsEl) {
            return;
        }
        const origin = document.getElementById('origin')?.value.trim() || '';
        const city = document.getElementById('destination')?.value.trim() || '';
        const budget = document.getElementById('budget')?.value.trim() || '';
        const days = getTripDays();

        originEl.textContent = origin || '____';
        originEl.classList.toggle('is-placeholder', !origin);
        cityEl.textContent = city || '____';
        cityEl.classList.toggle('is-placeholder', !city);
        factsEl.textContent = `${days > 0 ? days : '--'} DAYS · \u00a5${budget || '--'}`;
        renderPreviewSentence({ origin, city, budget, days });
    }

    // 行程摘要句（SLIM 修复 3）：把已填内容念成一句可以想象的话 ——
    // 「湛江 → 广州 · 10/10–10/12（3 天）· 预算 ¥2400 · 想看自然风光、美食」。
    // 旅游感来自对旅程的想象，不是装饰：这句是整页唯一在讲"你要出门了"的东西。
    // 只读已填值、只写文本；未填的项整段省略，不显示占位符、不编估算值
    // （沿用 findMealCost 立下的规矩）。全部未填时整行隐藏。
    function renderPreviewSentence({ origin, city, budget, days }) {
        const sentenceEl = document.getElementById('preview-sentence');
        if (!sentenceEl) {
            return;
        }
        const parts = [];
        if (origin && city) {
            parts.push(`${origin} → ${city}`);
        } else if (city) {
            parts.push(`目的地 ${city}`);
        } else if (origin) {
            parts.push(`${origin} 出发`);
        }
        const start = departureDateInput.value;
        const end = endDateInput.value;
        if (start && end && days > 0) {
            // stubDate 切的是 ISO 串（2026-10-11 → 10-11），自带连字符；
            // 直接拿它去拼连接号会得到「10-11–10-13」四个数字糊成一片。
            // 换成正斜杠，与页面其它地方的日期写法一致。
            const shortDate = iso => stubDate(iso).replace('-', '/');
            parts.push(`${shortDate(start)}–${shortDate(end)}（${days} 天）`);
        }
        if (budget) {
            parts.push(`预算 ¥${budget}`);
        }
        // 偏好读 chip 上的可见文案（自然风光），不是 input 的 value（自然风景）
        const wants = Array.from(preferenceGroup.querySelectorAll('input:checked'))
            .map(input => input.closest('label')?.textContent.trim())
            .filter(Boolean);
        if (wants.length) {
            parts.push(`想看${wants.join('、')}`);
        }
        sentenceEl.textContent = parts.join(' · ');
        sentenceEl.classList.toggle('hidden', parts.length === 0);
    }

    function bindLandingPreview() {
        // 日期是自绘控件，选中后走的是原生 input 的 change；文本框走 input。
        // 两个事件都听，免得漏掉自绘日历那条路径。
        ['origin', 'destination', 'budget', 'departureDate', 'endDate'].forEach(id => {
            const el = document.getElementById(id);
            if (!el) {
                return;
            }
            el.addEventListener('input', updateLandingPreview);
            el.addEventListener('change', updateLandingPreview);
        });
        updateLandingPreview();
    }

    function syncPreferenceValues() {
        const checkedValues = Array.from(preferenceGroup.querySelectorAll('input:checked'))
            .map(input => input.value);
        hiddenPreferences.value = checkedValues.join(',');
    }

    function buildTripPayload() {
        const hotelAnchor = document.getElementById('hotelAnchor').value.trim();
        const hotelContinuity = document.getElementById('hotelContinuity').value;
        const payload = {
            city: document.getElementById('destination').value.trim(),
            startDate: departureDateInput.value,
            endDate: endDateInput.value,
            budget: Number(document.getElementById('budget').value),
            preferences: hiddenPreferences.value.split(',').map(item => item.trim()).filter(Boolean),
            transportation: document.getElementById('transportation').value || null,
            accommodation: document.getElementById('accommodation').value,
            extraRequirements: document.getElementById('extraRequirements').value.trim()
        };
        // 出发城市现在真的进请求体了：填了才会算往返大交通、才会把第一天起点换成到达口岸
        const originCity = document.getElementById('origin')?.value.trim();
        if (originCity) {
            payload.originCity = originCity;
        }
        if (hotelAnchor) {
            payload.hotelAnchor = hotelAnchor;
        }
        if (hotelContinuity) {
            payload.hotelContinuity = hotelContinuity;
        }
        // 按天锚点由追问确认后写进 currentPayload，表单里没有对应输入框
        const dayHotelAnchors = currentPayload ? currentPayload.dayHotelAnchors : null;
        if (dayHotelAnchors && Object.keys(dayHotelAnchors).length) {
            payload.dayHotelAnchors = dayHotelAnchors;
        }
        return payload;
    }

    function validateTripPayload(payload) {
        if (!payload.city) {
            return '请先填写目的地城市';
        }
        if (!payload.startDate) {
            return '请先选择开始日期';
        }
        if (!payload.endDate) {
            return '请先选择结束日期';
        }
        if (payload.endDate < payload.startDate) {
            return '结束日期不能早于开始日期';
        }
        if (!payload.budget || payload.budget < 1) {
            return '总预算必须大于 0';
        }
        if (!payload.preferences.length) {
            return '请至少选择一个旅行偏好';
        }
        return '';
    }

    function initializeDefaultDates() {
        const today = new Date();
        const endDate = new Date(today);
        endDate.setDate(today.getDate() + 2);
        if (!departureDateInput.value) {
            departureDateInput.value = formatDateInput(today);
        }
        if (!endDateInput.value) {
            endDateInput.value = formatDateInput(endDate);
        }
        departureDateInput.min = formatDateInput(today);
        endDateInput.min = departureDateInput.value;
    }

    function syncDateRange() {
        const start = departureDateInput.value;
        const end = endDateInput.value;
        if (start && end && end < start) {
            endDateInput.value = start;
        }
        endDateInput.min = start || formatDateInput(new Date());
        updateDaysDisplay(`${getTripDays()} 天`);
    }

    // 天数翻牌（反馈单辅助创意 B）：纯展示层 —— 天数怎么算仍由 getTripDays() 决定，
    // 这里只负责怎么把它显示出来。牌面先翻下遮住旧值，翻到侧对视线（150ms）时换文本，
    // 下半程翻回时露出的已经是新值，所以中途换值看不见。
    function updateDaysDisplay(value) {
        if (daysDisplay.textContent === value) {
            return;
        }
        if (reducedMotionQuery.matches) {
            daysDisplay.textContent = value; // reduced-motion：不翻牌，直接换文本
            return;
        }
        window.clearTimeout(daysFlipTimer);
        daysDisplay.classList.remove('is-flipping');
        void daysDisplay.offsetWidth; // 强制重排，让动画能重放
        daysDisplay.classList.add('is-flipping');
        daysFlipTimer = window.setTimeout(() => {
            daysDisplay.textContent = value;
        }, 150);
    }

    function formatDateInput(date) {
        const year = date.getFullYear();
        const month = String(date.getMonth() + 1).padStart(2, '0');
        const day = String(date.getDate()).padStart(2, '0');
        return `${year}-${month}-${day}`;
    }

    function round(value, digits = 1) {
        const numeric = Number(value);
        if (!Number.isFinite(numeric)) {
            return 0;
        }
        const factor = 10 ** digits;
        return Math.round(numeric * factor) / factor;
    }

    function getTripDays() {
        const start = new Date(departureDateInput.value);
        const end = new Date(endDateInput.value);
        const diff = Math.round((end - start) / (1000 * 60 * 60 * 24));
        return Math.max(1, diff + 1 || 1);
    }

    // ---------- 自绘日期选择器 ----------
    // 起因：原生日期弹层的尺寸与显示格式（跟着浏览器语言走）都无法用 CSS 改。
    // 契约保持不变：原生 input 仍是唯一取值源与 min 约束载体，选中后照样 dispatch change，
    // 所以 syncDateRange / getTripDays / buildTripPayload 一行都不用改。
    // 本段代码在本文件末尾执行，晚于上方第 117 行的 initializeDefaultDates()，读得到初始值。

    const WEEKDAY_LABELS = ['日', '一', '二', '三', '四', '五', '六'];

    function formatDateDisplay(iso) {
        return iso ? iso.replace(/-/g, '/') : '';
    }

    const dateFields = Array.from(document.querySelectorAll('[data-date-field]')).map((root) => {
        const input = root.querySelector('input[type="date"]');
        const trigger = root.querySelector('.date-trigger');
        const valueEl = root.querySelector('.date-value');

        const popup = document.createElement('div');
        popup.className = 'date-popup hidden';
        popup.setAttribute('role', 'dialog');
        popup.setAttribute('aria-label', '选择日期');
        popup.innerHTML = `
            <div class="date-popup-head">
                <button type="button" class="date-nav date-nav-prev" aria-label="上个月">
                    <span class="date-nav-caret"></span>
                </button>
                <span class="date-popup-title"></span>
                <button type="button" class="date-nav date-nav-next" aria-label="下个月">
                    <span class="date-nav-caret"></span>
                </button>
            </div>
            <div class="date-popup-week">${WEEKDAY_LABELS.map((w) => `<span>${w}</span>`).join('')}</div>
            <div class="date-grid"></div>
        `;
        root.appendChild(popup);

        const anchor = input.value ? new Date(`${input.value}T00:00:00`) : new Date();
        const field = {
            input,
            trigger,
            valueEl,
            popup,
            titleEl: popup.querySelector('.date-popup-title'),
            gridEl: popup.querySelector('.date-grid'),
            prevBtn: popup.querySelector('.date-nav-prev'),
            nextBtn: popup.querySelector('.date-nav-next'),
            view: { year: anchor.getFullYear(), month: anchor.getMonth() }
        };

        trigger.addEventListener('click', () => {
            if (popup.classList.contains('hidden')) {
                openDateField(field);
            } else {
                closeDateField(field);
            }
        });
        field.prevBtn.addEventListener('click', () => shiftDateMonth(field, -1));
        field.nextBtn.addEventListener('click', () => shiftDateMonth(field, 1));
        popup.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') {
                event.preventDefault();
                closeDateField(field);
                trigger.focus();
                return;
            }
            const step = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 }[event.key];
            if (!step) {
                return;
            }
            const cells = Array.from(field.gridEl.querySelectorAll('[data-date]:not(:disabled)'));
            const index = cells.indexOf(document.activeElement);
            const next = cells[Math.min(Math.max(index + step, 0), cells.length - 1)];
            if (next) {
                event.preventDefault();
                next.focus();
            }
        });
        return field;
    });

    // 日期显示带星期（SLIM 修复 4.1）：2026/10/10 → 2026/10/10 周六。
    // 这里是日期文本的唯一写入点。取值源、min 约束、change 派发一律不动 ——
    // 星期是就地算出来的展示文本，不进请求、不进任何分支。
    function syncDateFieldDisplay(field) {
        const iso = field.input.value;
        const text = formatDateDisplay(iso);
        if (text) {
            const [year, month, day] = iso.split('-').map(Number);
            const weekday = WEEKDAY_LABELS[new Date(year, month - 1, day).getDay()];
            field.valueEl.innerHTML = `${text}<span class="date-weekday">周${weekday}</span>`;
        } else {
            field.valueEl.textContent = '选择日期';
        }
        field.valueEl.classList.toggle('is-empty', !text);
    }

    function openDateField(field) {
        dateFields.forEach((other) => {
            if (other !== field) {
                closeDateField(other);
            }
        });
        // 每次打开都把视图拉回当前值所在月份
        const anchor = field.input.value ? new Date(`${field.input.value}T00:00:00`) : new Date();
        field.view = { year: anchor.getFullYear(), month: anchor.getMonth() };
        renderDateMonth(field);
        field.popup.classList.remove('hidden');
        field.trigger.setAttribute('aria-expanded', 'true');
        // 弹层比字段宽，窄屏下要按"字段左缘到视口右缘"的剩余空间收窄，
        // 否则会越出视口右缘（375px 下实测会到 390）。
        // 只按 100vw 算 max-width 不够 —— 字段本身还带着外壳存根区与卡片内边距的偏移。
        const popupLeft = field.popup.getBoundingClientRect().left;
        const available = window.innerWidth - popupLeft - 16;
        field.popup.style.width = `${Math.max(240, Math.min(320, available))}px`;
        const focusTarget = field.gridEl.querySelector('.is-selected:not(:disabled)')
            || field.gridEl.querySelector('.is-today:not(:disabled)')
            || field.gridEl.querySelector('[data-date]:not(:disabled)');
        if (focusTarget) {
            focusTarget.focus();
        }
    }

    function closeDateField(field) {
        field.popup.classList.add('hidden');
        field.trigger.setAttribute('aria-expanded', 'false');
    }

    function shiftDateMonth(field, delta) {
        const shifted = new Date(field.view.year, field.view.month + delta, 1);
        field.view = { year: shifted.getFullYear(), month: shifted.getMonth() };
        renderDateMonth(field);
    }

    function renderDateMonth(field) {
        const { year, month } = field.view;
        field.titleEl.textContent = `${year}年${month + 1}月`;

        const min = field.input.min; // 由 syncDateRange 维护：结束日期不得早于开始日期
        const firstWeekday = new Date(year, month, 1).getDay();
        const daysInMonth = new Date(year, month + 1, 0).getDate();
        const prevMonthDays = new Date(year, month, 0).getDate();
        const todayIso = formatDateInput(new Date());

        const cells = [];
        for (let i = firstWeekday - 1; i >= 0; i -= 1) {
            cells.push({ day: prevMonthDays - i, offset: -1 });
        }
        for (let d = 1; d <= daysInMonth; d += 1) {
            cells.push({ day: d, offset: 0 });
        }
        let trailing = 1;
        while (cells.length % 7 !== 0) {
            cells.push({ day: trailing, offset: 1 });
            trailing += 1;
        }

        field.gridEl.innerHTML = cells.map((cell) => {
            const iso = formatDateInput(new Date(year, month + cell.offset, cell.day));
            const classes = ['date-cell'];
            if (cell.offset !== 0) {
                classes.push('is-outside');
            }
            if (iso === todayIso) {
                classes.push('is-today');
            }
            if (iso === field.input.value) {
                classes.push('is-selected');
            }
            const disabled = min && iso < min ? ' disabled' : '';
            return `<button type="button" class="${classes.join(' ')}" data-date="${iso}"${disabled}>${cell.day}</button>`;
        }).join('');

        field.gridEl.querySelectorAll('[data-date]').forEach((cell) => {
            cell.addEventListener('click', () => selectDate(field, cell.dataset.date));
        });
    }

    function selectDate(field, iso) {
        if (field.input.value !== iso) {
            field.input.value = iso;
            // 走既有链路：syncDateRange 据此更新天数（触发翻牌）与结束日期的 min
            field.input.dispatchEvent(new Event('change', { bubbles: true }));
        }
        // 结束日期可能被 syncDateRange 夹到开始日期上，所以两个显示都要重算
        dateFields.forEach(syncDateFieldDisplay);
        closeDateField(field);
        field.trigger.focus();
    }

    // 点击弹层与触发器之外的地方就收起
    document.addEventListener('click', (event) => {
        dateFields.forEach((field) => {
            if (!field.popup.classList.contains('hidden')
                && !field.popup.contains(event.target)
                && !field.trigger.contains(event.target)) {
                closeDateField(field);
            }
        });
    });

    dateFields.forEach(syncDateFieldDisplay);

    function setLoading(isLoading) {
        submitBtn.disabled = isLoading;
        progressPanel.classList.toggle('hidden', !isLoading);
        statusText.textContent = isLoading ? '正在生成中...' : '';
        // 第 1 幕「受理章」的 class 钩子（反馈单红线：JS 只加 class 钩子与展示字符串，
        // 请求 / 分支 / 渲染流程一行不动）
        landingPage.classList.toggle('is-loading', isLoading);
    }

    function setTripAdjustLoading(isLoading) {
        editTripBtn.disabled = isLoading;
        editTripBtn.textContent = isLoading ? '调整中...' : '编辑行程';
        tripAdjustOverlay.classList.toggle('hidden', !isLoading);
    }

    // 等待期文案按后端真实做的事按序播一遍（一轮约 27 秒，之后停在最后一步继续等）。
    // 原来只有两句、且百分比爬到 90 就 stopProgress()，剩下的 30~150 秒画面一动不动，
    // 委托单又被压到 40% 透明，看着就像白屏卡死（2026-10-10 反馈）。
    const PROGRESS_STAGES = [
        ['STEP 1/6 检索景点', '正在按偏好检索候选景点...'],
        ['STEP 2/6 查询天气', '正在查这几天的天气与气温...'],
        ['STEP 3/6 挑选住宿', '正在按价位与远近挑住宿...'],
        ['STEP 4/6 串联路线', '正在串每天的走法与通勤...'],
        ['STEP 5/6 反思修正', '正在按预算和天气回头修正...'],
        ['STEP 6/6 核算打印', '正在算账并打印行程票...']
    ];
    const PROGRESS_TICK_MS = 900;
    const PROGRESS_TICKS_PER_STAGE = 5;

    function startFakeProgress() {
        stopProgress();
        let current = 6;
        let tick = 0;
        updateProgress(current, PROGRESS_STAGES[0][0], PROGRESS_STAGES[0][1]);
        progressTimer = setInterval(() => {
            tick += 1;
            // 前段爬得快，60% 之后每跳最多 1%，且永远停在 97 以下，给"完成"留最后一步
            if (current < 60) {
                current = Math.min(current + 7 + Math.floor(Math.random() * 6), 60);
            } else if (Math.random() < 0.4) {
                current = Math.min(current + 1, 97);
            }
            const stage = PROGRESS_STAGES[Math.min(Math.floor(tick / PROGRESS_TICKS_PER_STAGE),
                                                   PROGRESS_STAGES.length - 1)];
            updateProgress(current, stage[0], stage[1]);
        }, PROGRESS_TICK_MS);
    }

    function completeProgress() {
        stopProgress();
        updateProgress(100, 'DONE 6/6', '旅行计划已生成完成');
    }

    function stopProgress() {
        if (progressTimer) {
            clearInterval(progressTimer);
            progressTimer = null;
        }
    }

    function updateProgress(value, stageText, noteText) {
        progressFill.style.width = `${value}%`;
        progressPercent.textContent = `${value}%`;
        progressLabel.textContent = noteText;
        if (progressStage && stageText) {
            progressStage.textContent = stageText;
        }
    }

    function showError(message) {
        statusText.classList.add('error');
        statusText.textContent = '生成失败';
        errorBox.classList.remove('hidden');
        errorBox.textContent = message;
        errorToast.classList.remove('hidden');
        errorToast.textContent = message;
        errorBox.scrollIntoView({ behavior: 'smooth', block: 'center' });
        window.setTimeout(() => {
            errorToast.classList.add('hidden');
            errorToast.textContent = '';
        }, 5000);
    }

    function clearError() {
        statusText.classList.remove('error');
        statusText.textContent = '';
        errorBox.classList.add('hidden');
        errorBox.textContent = '';
        errorToast.classList.add('hidden');
        errorToast.textContent = '';
    }

    function renderResult(data, payload) {
        currentTripData = data;
        currentPayload = payload;
        document.getElementById('summary-title').textContent = `${payload.city}旅行计划`;
        document.getElementById('summary-text').textContent = buildSummaryText(data, payload);
        renderTicket(data, payload);
        renderOverview(data, payload);
        renderPlanningNotes(data, payload);
        renderBudget(data, payload);
        renderWeather(data, payload);
        renderDays(data, payload);
        renderMapTabs(data);
        renderMap(data, payload, 0);
        renderExtraRequirements(payload);
        setActiveNav('overview-section');
        // 方案一落定就写盘。这里是**唯一**的落点：新方案（走 resetAssistant 清了对话）
        // 与 /adjust 改完（对话留着）都从这里过。
        persistSession();
    }

    /* ---------- 旅行助手页 ----------
     *
     * 原本挂在结果页底部的「方案追问」抽出来，变成这一屏对话。
     * **不落库、不引入路由**：进来时直接读内存里的 currentTripData / currentPayload
     * （同文档切视图，这两个变量还在）。所以刷新即失效，与抽出来之前一致。
     *
     * **消息堆在屏幕上，但模型看不到历史**——每次请求只带当前这一句
     * （后端 ask_agent.py 也是单条 HumanMessage、没接 checkpointer）。
     * 这是"聊天样子的单轮"，是有意为之，不是漏。
     */

    // 进助手前结果页滚到哪了。回来时还原——用户是从某一节跳去问问题的，
    // 回来把他扔回页顶等于把上下文弄丢。
    // 「方案」与「助手」互斥占右半边。**#result-page 一直不藏**，所以左栏天然留着。
    function showAssistantView() {
        dashboardMain.classList.add('hidden');
        assistantPanel.classList.remove('hidden');
        // 锁页高 + 藏页脚风景带/坐标读数，见 assistant.css 那一段
        resultPage.classList.add('is-assistant');
        assistantEntry.classList.add('is-active');
        // 左栏那五个高亮的是"方案里的哪一节"，助手页上它们都不该亮
        setActiveNav('');
    }

    function showPlanView() {
        assistantPanel.classList.add('hidden');
        dashboardMain.classList.remove('hidden');
        resultPage.classList.remove('is-assistant');
        assistantEntry.classList.remove('is-active');
    }

    function openAssistant() {
        if (!currentTripData || !currentPayload) {
            showError('请先生成一次旅行计划，再使用旅行助手');
            return;
        }
        showAssistantView();
        document.getElementById('ask-question').focus();
    }

    // 宽屏上助手页没有「← 返回方案」：左栏一直在，点上面任一导航项就回去了
    // （见 navItems 的 click 处理）。窄屏（≤1100px）左栏收起了，这个按钮才露面，
    // 所以这条绑定只为窄屏服务。
    function closeAssistant() {
        showPlanView();
        syncNavToScroll();
    }

    /* 自动滚到底只在**用户本来就贴着底部**的时候做。
     *
     * 原来是无条件滚，而打字机每 16ms 调一次、工具条目每来一条调一次——
     * 用户在流跑完之前想往上翻旧消息，会被一帧一帧地拽回底部，翻不动。
     * "跟随最新内容"变成"锁死在底部"，这是 bug 不是缺功能。
     *
     * 状态由消息区自己的 scroll 事件维护，而不是每次现算：现算的话，
     * 新内容一 append 上去 scrollHeight 就变高了，"离底多远"立刻失真，
     * 明明没动过的用户会被判成"已经翻上去了"。
     */
    const ASSISTANT_STICK_SLACK = 40;   // 差一点点也算贴着底，别要求像素级精确

    let assistantStick = true;

    function isAssistantAtBottom() {
        return assistantMessages.scrollHeight - assistantMessages.scrollTop
            - assistantMessages.clientHeight <= ASSISTANT_STICK_SLACK;
    }

    assistantMessages.addEventListener('scroll', () => {
        assistantStick = isAssistantAtBottom();
    }, { passive: true });

    // force 只给"轮次边界"用（用户刚发了一句、或刚重置），那时他要看的是新一轮的内容。
    function scrollAssistantToBottom(force = false) {
        if (!force && !assistantStick) {
            return;
        }
        assistantMessages.scrollTop = assistantMessages.scrollHeight;
    }

    // 空态提示只在"一条消息都没有"时出现，第一条进来就撤掉。
    function clearAssistantEmpty() {
        if (assistantEmpty.parentNode) {
            assistantEmpty.remove();
        }
    }

    // 返回气泡本身，后续（工具条目、打字机）要往里追加子节点。
    function appendMessage(role, text = '') {
        const bubble = document.createElement('div');
        bubble.className = `msg msg-${role}`;
        if (text) {
            bubble.textContent = text;
        }
        clearAssistantEmpty();
        assistantMessages.appendChild(bubble);
        // 强制：appendMessage 只在轮次边界被调用（用户刚发问、建待答气泡、报错），
        // 那时用户要看的就是这一条，不管他之前翻到哪了。
        scrollAssistantToBottom(true);
        return bubble;
    }

    /* 「已记住：不能吃辣」。**只报本次真的新存进去的**（后端算好的），
     * 重复说的不报——每次都弹会变成噪音，用户会开始无视它，那比不显示更糟。
     *
     * 挂在回答气泡里面、跟着打字机一起出现：它是这条回答的附注。
     * 用 textContent 而不是拼 HTML：偏好原文来自用户自己说的话，直接插进
     * innerHTML 就等于开了个注入口。 */
    function appendMemoryNote(bubble, items) {
        const note = document.createElement('div');
        note.className = 'msg-note';
        note.textContent = `已记住：${items.join('、')}`;
        bubble.appendChild(note);
    }

    // 换一份新方案 = 换一趟旅行，旧对话说的都是别的行程，留着只会误导。
    // （方案被 /adjust 改掉时**不**清——那种情况用户自己知道刚改过什么。）
    function resetAssistant() {
        // 卡片容器住在消息区里，清空时会被一起冲掉——它的 DOM 是 index.html 的固定节点，
        // 没了 getElementById 就返回 null，后面 show/hide 全会崩。所以要捡回来。
        assistantMessages.innerHTML = '';
        assistantMessages.append(assistantEmpty, askCards);
        assistantStick = true;   // 清空了就是空的，谈不上"用户翻到上面去了"
        // 短期记忆跟着 DOM 一起清：屏幕上没有了，模型眼里也不该还留着。
        chatHistory = [];
        document.getElementById('ask-question').value = '';
        document.getElementById('ask-status').textContent = '';
        hideAskConfirmation();
        persistSession();
    }

    /* ---------- 短期记忆的落盘：sessionStorage ----------
     *
     * 存的是**三样一起**：方案、原始请求、对话（docs/ASK_MULTITURN_MEMORY.md §2.1）。
     * 只存对话是没用的——每轮追问都要带 tripPlan，刷新后方案没了，对话留着也接不上。
     *
     * 为什么是 sessionStorage 而不是 localStorage：助手的定位是"方案生成之后的下一轮"
     * （见 ASK_SCOPE_OPENING.md §三），**关掉标签页 = 方案没了 = 对话也没用了**，
     * 持久化对话本来就没意义。顺带还不留陈旧数据、多标签页不打架。
     * 哪天助手能脱离方案单独用，这里换一个词就行。
     */

    const SESSION_KEY = 'travel-assistant.session';
    // 结构一改就作废旧数据。不加的话，布局变过之后老结构会被当成新结构去渲染，
    // 报出来的是一个跟真实原因无关的错。
    const SESSION_VERSION = 1;
    // 发给模型的历史窗口。与后端 _HISTORY_MAX_TURNS 同一个数——前端截是"别发没用的东西"，
    // 后端也必须截（不能信任客户端），两处都要有。
    const HISTORY_SEND_TURNS = 6;

    function persistSession() {
        if (!currentTripData || !currentPayload) {
            return;
        }
        try {
            sessionStorage.setItem(SESSION_KEY, JSON.stringify({
                version: SESSION_VERSION,
                tripPlan: currentTripData,
                request: currentPayload,
                history: chatHistory
            }));
        } catch (error) {
            // 配额满、隐私模式禁写：追问本身还能用，别为存不下来把主流程搞挂。
            console.warn('会话落盘失败（不影响本轮追问）:', error);
        }
    }

    function clearSession() {
        try {
            sessionStorage.removeItem(SESSION_KEY);
        } catch (error) {
            console.warn('会话清理失败:', error);
        }
    }

    function readSession() {
        let raw = null;
        try {
            raw = sessionStorage.getItem(SESSION_KEY);
        } catch (error) {
            console.warn('会话读取失败:', error);
            return null;
        }
        if (!raw) {
            return null;
        }
        let stored = null;
        try {
            stored = JSON.parse(raw);
        } catch (error) {
            clearSession();     // 半截 JSON 留着也没用，下次还会再解析失败一遍
            return null;
        }
        if (!stored || stored.version !== SESSION_VERSION
            || !stored.tripPlan || !stored.request) {
            clearSession();
            return null;
        }
        return stored;
    }

    // 只发最近几轮。前端截是省流量，**后端也截**——不能信任客户端，
    // 构造一个一万轮的历史就能把 prompt 打爆（那份文档的判据 7）。
    function historyForRequest() {
        return chatHistory.slice(-HISTORY_SEND_TURNS * 2);
    }

    // 刷新后把对话重建回屏幕上。刻意不走 appendMessage：那个每次都会
    // 强制滚到底，而这里是在页面加载时一次性铺完，应该停在顶部。
    function restoreConversation() {
        if (!chatHistory.length) {
            return;
        }
        clearAssistantEmpty();
        for (const turn of chatHistory) {
            const bubble = document.createElement('div');
            bubble.className = `msg msg-${turn.role === 'user' ? 'user' : 'assistant'}`;
            bubble.textContent = turn.content;
            assistantMessages.appendChild(bubble);
        }
        assistantStick = true;
    }

    // 首页表单要跟着恢复出来的请求走。不回填的话，刷新后点「返回首页」看到的是
    // 默认日期和空目的地，而方案页上明明摆着一份杭州的行程——两处对不上，
    // 用户照着改一改再提交，出来的是另一趟旅行。
    function applyRequestToForm(payload) {
        const setValue = (id, value) => {
            const el = document.getElementById(id);
            if (el && value !== undefined && value !== null) {
                el.value = value;
            }
        };
        setValue('origin', payload.originCity);
        setValue('destination', payload.city);
        setValue('departureDate', payload.startDate);
        setValue('endDate', payload.endDate);
        setValue('budget', payload.budget);
        setValue('transportation', payload.transportation);
        setValue('accommodation', payload.accommodation);
        setValue('extraRequirements', payload.extraRequirements);
        setValue('hotelAnchor', payload.hotelAnchor);
        setValue('hotelContinuity', payload.hotelContinuity);
        const wanted = new Set(payload.preferences || []);
        preferenceGroup.querySelectorAll('input[type="checkbox"]').forEach(box => {
            box.checked = wanted.has(box.value);
        });
        syncPreferenceValues();
        syncDateRange();
        updateLandingPreview();
    }

    // 刷新后把这一趟接回来：方案、原始请求、对话三样一起（§2.1 的三层漏斗）。
    // 走 renderResult 而不是另写一套渲染——它是方案落屏的唯一入口。
    function restoreSession() {
        const stored = readSession();
        if (!stored) {
            return;
        }
        // 落盘的东西也要过一遍形状检查：sessionStorage 是用户能改的，
        // 半条没答案的记录会让后端的成对截断把整段历史丢掉。
        chatHistory = (Array.isArray(stored.history) ? stored.history : []).filter(
            turn => turn && typeof turn.content === 'string'
                && (turn.role === 'user' || turn.role === 'assistant'));
        applyRequestToForm(stored.request);
        renderResult(stored.tripPlan, stored.request);
        restoreConversation();
        landingPage.classList.add('hidden');
        resultPage.classList.remove('hidden');
    }


    /* ---------- 流式追问（SSE） ----------
     *
     * 后端 `POST /api/trips/ask/stream` 推三种事件：`trace`（每调一次工具一条）、
     * `answer`（完整答案，一条）、`error`。协议见 docs/ASSISTANT_CHAT_DESIGN.md §3.4。
     *
     * **这里不能用 `EventSource`**：它只支持 GET，而方案必须走 POST body
     * （整份 tripPlan 都在 body 里）。所以自己拿 `response.body.getReader()` 读字节流、
     * 按 SSE 规范切帧。
     */

    // 助手气泡的固定骨架：上面挂工具条目，下面写答案。两者要能随后续事件分头往里塞。
    function appendPendingBubble() {
        const bubble = appendMessage('assistant');
        const tools = document.createElement('div');
        tools.className = 'msg-tools';
        const text = document.createElement('div');
        text.className = 'msg-text';
        bubble.append(tools, text);
        const pending = { bubble, tools, text, count: 0, running: null };
        // 一开始还没有任何工具条目，先只有这颗呼吸的点——聊天气泡里的"正在输入"。
        pending.running = document.createElement('div');
        pending.running.className = 'msg-tool is-running';
        pending.running.textContent = '正在思考…';
        tools.appendChild(pending.running);
        return pending;
    }

    // 那颗呼吸的点**不是**某一次工具调用，是"这轮还在跑"本身。
    // 每来一条真的工具条目就把它挪到末尾，让完成的条目按时间从上往下排。
    function moveRunningEntry(pending) {
        if (pending.count > 0) {
            pending.running.textContent = '正在继续…';
        }
        pending.tools.appendChild(pending.running);
    }

    function toolEntryText(trace) {
        const action = trace.action || trace.agentName || '调用工具';
        const input = (trace.inputSummary || '').trim();
        return input ? `${action} · ${input}` : action;
    }

    // 答案到手 = 这一轮的工具阶段结束，那颗呼吸的点该撤了。
    // 不撤的话它会一直转下去，看起来像还在跑——而这一轮其实已经结束了。
    function finishPendingBubble(pending) {
        if (pending.running && pending.running.parentNode) {
            pending.running.remove();
        }
    }

    function appendToolEntry(pending, trace) {
        const entry = document.createElement('div');
        entry.className = 'msg-tool';
        // 只把"这次没取到数"标红（检索失败、模型输出解析不了）。
        // not_found 不标——"库里没有"是一个正常结论，不是故障。
        if (trace.status === 'failed' || trace.status === 'fallback') {
            entry.classList.add('is-failed');
        }
        entry.textContent = toolEntryText(trace);
        // 工具带回来的原文收在悬停提示里：行内铺开会把聊天流冲散，
        // 但"答案是根据什么说的"要能查到。
        if (trace.outputSummary) {
            entry.title = trace.outputSummary;
        }
        pending.tools.insertBefore(entry, pending.running);
        pending.count += 1;
        moveRunningEntry(pending);
        scrollAssistantToBottom();
    }

    // 切一帧 SSE。`raw` 是两条空行之间的那一段，形如 `event: trace\ndata: {...}`。
    function parseSseFrame(raw) {
        let event = 'message';
        const dataLines = [];
        for (const line of raw.split(/\r?\n/)) {
            if (!line || line.startsWith(':')) {
                continue;   // 空行与注释行（含心跳）都跳过
            }
            const colon = line.indexOf(':');
            const field = colon === -1 ? line : line.slice(0, colon);
            let value = colon === -1 ? '' : line.slice(colon + 1);
            if (value.startsWith(' ')) {
                value = value.slice(1);   // 规范：冒号后紧跟的一个空格是分隔符，不算数据
            }
            if (field === 'event') {
                event = value;
            } else if (field === 'data') {
                dataLines.push(value);
            }
        }
        if (!dataLines.length) {
            return null;
        }
        try {
            return { event, data: JSON.parse(dataLines.join('\n')) };
        } catch (error) {
            console.error('Unparsable SSE frame:', raw, error);
            return null;
        }
    }

    // 读完一整条流，边读边把工具条目渲染出来。返回 answer 帧的载荷（没收到就是 null）。
    async function readAskStream(response, pending) {
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let answer = null;
        while (true) {
            const { value, done } = await reader.read();
            if (done) {
                break;
            }
            buffer += decoder.decode(value, { stream: true });
            // 帧以空行分隔。最后一段常常被网络切在半路，pop 出来留到下一轮再拼——
            // 直接对整段 split 会把半截 JSON 当帧解析，报一堆解析错。
            const frames = buffer.split(/\r?\n\r?\n/);
            buffer = frames.pop();
            for (const raw of frames) {
                const frame = parseSseFrame(raw);
                if (!frame) {
                    continue;
                }
                if (frame.event === 'trace') {
                    appendToolEntry(pending, frame.data);
                } else if (frame.event === 'answer') {
                    answer = frame.data;
                } else if (frame.event === 'error') {
                    throw new Error(frame.data.message || '追问失败');
                }
            }
        }
        return answer;
    }

    /* ---------- 假流式打字机 ----------
     *
     * 答案**不是**逐 token 从模型流过来的：模型最后一轮吐的是规则 9 约定的 JSON 信封，
     * 边生成边推会让用户先看到 `{"intent":"answer","answer":"` 这堆脚手架。
     * 所以服务端等它整条生成完一次性推来，这里在本地把它"播"一遍。理由见设计文档 §3.4。
     */
    /* ---------- 改方案确认卡：消息流里的一条 ----------
     *
     * 卡片 DOM（#ask-confirm / #ask-hotel-move）是 index.html 里的固定节点，
     * ID 与事件绑定一律没动；这里只做一件事——每轮把它**挪到消息流最末尾**，
     * 让"这张卡接在哪句答案后面"看得出来。见设计文档 §3.5。
     */

    const askCards = document.getElementById('ask-cards');

    // 卡片要等答案打完再显形。答案还在逐字写的时候就弹"要按这句话重新生成吗"，
    // 用户还没读完就被催着做决定。
    // askTurnId 用来作废过期的显形：用户等不及先问了下一句，上一句挂的回调就不该再弹卡。
    let askTurnId = 0;

    function moveAskCardsToEnd() {
        clearAssistantEmpty();
        assistantMessages.appendChild(askCards);
        scrollAssistantToBottom();
    }

    function revealAskCards() {
        askCards.classList.remove('hidden');
        scrollAssistantToBottom();
    }

    const TYPEWRITER_TICK_MS = 16;      // ≈ 60fps
    const TYPEWRITER_MS_PER_CHAR = 18;
    const TYPEWRITER_MIN_MS = 700;      // 短答案也播这么久，不然"唰"一下什么都看不清
    const TYPEWRITER_MAX_MS = 2600;     // 长答案到此为止，不能让用户等打字

    const PREFERS_REDUCED_MOTION = window.matchMedia
        && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    function playTypewriter(element, text, onDone) {
        // 关了动效就直出。打字机是纯装饰，对这部分用户它只是"答案迟迟不出现"。
        if (PREFERS_REDUCED_MOTION) {
            element.textContent = text;
            if (onDone) {
                onDone();
            }
            return;
        }
        // 按码点切，别把 emoji / 代理对劈成两半（那样会渲染出半个乱码方块）。
        const chars = Array.from(text);
        const duration = Math.min(TYPEWRITER_MAX_MS,
            Math.max(TYPEWRITER_MIN_MS, chars.length * TYPEWRITER_MS_PER_CHAR));
        const step = Math.max(1, Math.round(chars.length / (duration / TYPEWRITER_TICK_MS)));
        let shown = 0;
        element.textContent = '';
        element.classList.add('is-typing');
        const timer = setInterval(() => {
            shown = Math.min(chars.length, shown + step);
            element.textContent = chars.slice(0, shown).join('');
            scrollAssistantToBottom();
            if (shown >= chars.length) {
                clearInterval(timer);
                element.classList.remove('is-typing');
                if (onDone) {
                    onDone();
                }
            }
        }, TYPEWRITER_TICK_MS);
    }

    async function askAboutCurrentTrip() {
        const input = document.getElementById('ask-question');
        const question = input.value.trim();
        const status = document.getElementById('ask-status');
        const button = document.getElementById('ask-submit');
        // 上一轮还在跑就别再发。点按钮那条路走不到这儿（disabled 的按钮不触发 click），
        // 但 Enter 是直接调本函数的，绕过了按钮的 disabled。
        if (button.disabled) {
            return;
        }
        if (!question) {
            showError('请先输入想追问的内容');
            return;
        }
        if (!currentTripData || !currentPayload) {
            showError('请先生成一次旅行计划，再进行追问');
            return;
        }

        // 不再写 button.textContent：它现在是个圆形图标钮，写字会把里面的箭头 SVG 冲掉。
        // "在跑"由左边的状态行 + 钮变灰呼吸表达。
        button.disabled = true;
        hideAskConfirmation();
        status.textContent = '正在读方案并查数据，约需几秒';
        input.value = '';
        appendMessage('user', question);
        const pending = appendPendingBubble();
        try {
            const response = await fetch('/api/trips/ask/stream', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    question: question,
                    tripPlan: currentTripData,
                    request: currentPayload,
                    // 当前这句**不在** history 里（后端会单独拼 Human），
                    // 所以这里发的是"这句之前"的对话。
                    history: historyForRequest()
                })
            });
            if (!response.ok) {
                const data = await response.json().catch(() => null);
                console.error('Trip ask request failed:', data);
                throw new Error(formatErrorMessage(data) || '追问失败');
            }
            const data = await readAskStream(response, pending);
            if (!data) {
                // 流正常结束但没给 answer 帧——留个空壳气泡比报错更让人摸不着头脑。
                throw new Error('追问没有返回结果');
            }
            status.textContent = '';
            finishPendingBubble(pending);
            // 成功之后才记进历史，而且**一问一答一起记**。
            // 失败的那一轮用户重问一次就行；只记 Human 不记 AI 更糟——后端是从末尾
            // 往回配对的，末尾那条配不上会把**整段**历史一起丢掉。
            // 存的是 data.answer（解析后的答案），不是模型吐出的 JSON 信封原文，见 §3.2。
            if (data.answer) {
                chatHistory.push({ role: 'user', content: question },
                    { role: 'assistant', content: data.answer });
                persistSession();
            }
            const change = showAskConfirmation(data);
            // 先把 turnId 记下来，再开打字机。hideAskConfirmation（下一句、或点了确认/算了）
            // 会把它 +1，于是这条过期回调自己就失效了，不会把旧卡弹到新答案下面。
            const turnId = ++askTurnId;
            // 这次新记下的长期偏好。**排在打字机之后**出现：它说的是"我刚记下了"，
            // 得等用户看完回答才轮得到它。
            const remembered = Array.isArray(data.recordedPreferences)
                ? data.recordedPreferences : [];
            playTypewriter(pending.text, data.answer || '未返回回答', () => {
                if (remembered.length) {
                    appendMemoryNote(pending.bubble, remembered);
                }
                if (change && turnId === askTurnId) {
                    revealAskCards();
                }
            });
        } catch (error) {
            pending.bubble.remove();
            status.textContent = '';
            hideAskConfirmation();
            // 不在聊天里同时弹 toast：气泡本身就是错误展示，两个一起出太吵。
            appendMessage('error', error.message);
        } finally {
            button.disabled = false;
            scrollAssistantToBottom();
        }
    }

    let pendingAskChange = null;

    function normalizePinHints(value) {
        if (Array.isArray(value)) {
            return value.map(name => (name || '').trim()).filter(Boolean);
        }
        const single = (value || '').trim();
        return single ? [single] : [];
    }

    // 返回建好的 change（没卡就是 null）——调用方拿它判断要不要在打字机收尾时显形。
    function showAskConfirmation(data) {
        if (data.intent !== 'need_change' || !data.adjustmentPrompt) {
            hideAskConfirmation();
            return null;
        }
        pendingAskChange = {
            adjustmentPrompt: data.adjustmentPrompt,
            anchorHint: data.anchorHint || null,
            anchorDay: Number.isInteger(data.anchorDay) ? data.anchorDay : null,
            pinHints: normalizePinHints(data.pinHints || data.pinHint),
            pinDay: Number.isInteger(data.pinDay) ? data.pinDay : null,
            moveHotel: null
        };
        moveAskCardsToEnd();
        // 指定了必去景点、又没提酒店 → 先问一句要不要把酒店也挪过去（多一张卡，仍在同一次提交内）
        if (pendingAskChange.pinHints.length && !pendingAskChange.anchorHint) {
            showHotelMoveCard();
            return pendingAskChange;
        }
        showFinalAskConfirm();
        return pendingAskChange;
    }

    function showHotelMoveCard() {
        const dayLabel = pendingAskChange.pinDay === null ? '这趟' : `第 ${pendingAskChange.pinDay + 1} 天`;
        document.getElementById('ask-hotel-move-text').textContent =
            `${dayLabel}要必去「${pendingAskChange.pinHints.join('、')}」——要不要把酒店也挪到它们附近？`
            + '（不挪就保住现在的酒店，只把景点加进去）';
        document.getElementById('ask-hotel-move').classList.remove('hidden');
        document.getElementById('ask-confirm').classList.add('hidden');
    }

    function chooseHotelMove(move) {
        if (!pendingAskChange) {
            return;
        }
        pendingAskChange.moveHotel = move;
        document.getElementById('ask-hotel-move').classList.add('hidden');
        showFinalAskConfirm();
    }

    function showFinalAskConfirm() {
        const change = pendingAskChange;
        const where = [];
        if (change.anchorHint) {
            where.push(`住宿位置：${change.anchorHint}${change.anchorDay === null ? '（整趟）' : `（第 ${change.anchorDay + 1} 天）`}`);
        }
        if (change.pinHints.length) {
            const dayLabel = change.pinDay === null ? '某天' : `第 ${change.pinDay + 1} 天`;
            const moveNote = change.moveHotel === true ? '，酒店也挪过去'
                : change.moveHotel === false ? '，酒店不变' : '';
            where.push(`必去：${dayLabel}的${change.pinHints.join('、')}${moveNote}`);
        }
        const whereText = where.length ? `（${where.join('；')}）` : '';
        document.getElementById('ask-confirm-text').textContent =
            `要按这句话重新生成方案吗${whereText}：${change.adjustmentPrompt}`;
        document.getElementById('ask-confirm').classList.remove('hidden');
    }

    function hideAskConfirmation() {
        pendingAskChange = null;
        // 作废还挂着的显形回调：这张卡这次不弹了，过了这一轮也不该再弹。
        askTurnId += 1;
        askCards.classList.add('hidden');
        document.getElementById('ask-confirm').classList.add('hidden');
        document.getElementById('ask-hotel-move').classList.add('hidden');
    }

    async function confirmAskChange() {
        const change = pendingAskChange;
        if (!change) {
            return;
        }
        hideAskConfirmation();
        // 把 agent 摘出的 anchor/pin + 挪酒店选择转达给 /adjust，写哪一格由后端定
        await runTripAdjustment(change.adjustmentPrompt, change.anchorHint, change.anchorDay,
            change.pinHints, change.pinDay, change.moveHotel === true);
    }

    /* ---------- 改方案输入弹窗 ----------
     *
     * 替代 `window.prompt`。原生那个的字体、按钮、宽度**全归浏览器管**，CSS 碰不到，
     * 中文在 mac 上还会掉到宋体那一路——跟页面是两个世界。自己画一个就跟着页面走了。
     *
     * 它不是一段同步代码，所以包成 Promise：调用方 await 一句，
     * 回来的是用户输入的那句，或者 null（取消 / Esc / 点遮罩外面）。
     */

    const adjustPromptOverlay = document.getElementById('adjust-prompt-overlay');
    const adjustPromptInput = document.getElementById('adjust-prompt-input');
    let adjustPromptResolve = null;

    function closeAdjustPrompt(value) {
        adjustPromptOverlay.classList.add('hidden');
        adjustPromptInput.value = '';
        // 先取出来再清空：resolve 里可能又开一次弹窗（比如校验没过），
        // 后赋的值不能被这一句顺手清掉。
        const resolve = adjustPromptResolve;
        adjustPromptResolve = null;
        if (resolve) {
            resolve(value);
        }
    }

    function askAdjustmentPrompt() {
        adjustPromptOverlay.classList.remove('hidden');
        adjustPromptInput.focus();   // 摘掉 hidden 就是可见了，这一步不用等下一帧
        return new Promise(resolve => {
            adjustPromptResolve = resolve;
        });
    }

    document.getElementById('adjust-prompt-cancel').addEventListener('click', () => closeAdjustPrompt(null));
    document.getElementById('adjust-prompt-ok').addEventListener('click', () => {
        const value = adjustPromptInput.value.trim();
        if (!value) {
            showError('改计划要求不能为空');
            return;   // 不关窗：让用户接着改，而不是把输入框连同内容一起收掉
        }
        closeAdjustPrompt(value);
    });
    // 点遮罩本身才算取消。用 mousedown 而不是 click：click 的 target 是
    // 按下与抬起**共同**的祖先，从面板里按下、拖到遮罩上抬起会误判成点了遮罩。
    adjustPromptOverlay.addEventListener('mousedown', (event) => {
        if (event.target === adjustPromptOverlay) {
            closeAdjustPrompt(null);
        }
    });
    // 挂在遮罩上而不是 document 上：事件从 textarea 冒泡上来，够用，
    // 又不会在弹窗关着的时候跟别处的 Enter/Esc 抢。
    adjustPromptOverlay.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            closeAdjustPrompt(null);
            return;
        }
        if (event.key !== 'Enter' || event.shiftKey) {
            return;
        }
        // 中文输入法选词的那个回车不算发送，否则半句话就出去了
        if (event.isComposing || event.keyCode === 229) {
            return;
        }
        event.preventDefault();
        document.getElementById('adjust-prompt-ok').click();
    });

    /* ---------- 我的偏好（docs/ASK_LONGTERM_MEMORY.md §2.3）----------
     *
     * 列出这个人已经记住的偏好，逐条能删。**只做列表与删除**：编辑不做（§2.2.2 定了
     * 偏好没有"修改"这个动作——要改就删掉、再说一条新的），清空/分页/搜索也不做。
     */
    const preferencesOverlay = document.getElementById('preferences-overlay');
    const preferencesList = document.getElementById('preferences-list');
    const preferencesEmpty = document.getElementById('preferences-empty');

    // 来源那行小字：回答"这条哪来的"（§2.3.2）。用户判断哪条记错了，第一个线索就是它。
    const PREFERENCE_SOURCE_TEXT = { chat: '来自聊天', form: '来自表单备注' };

    function openPreferences() {
        preferencesOverlay.classList.remove('hidden');
        // 焦点放进弹层里：一是键盘用户落点正确，二是**Esc 才收得到**——
        // keydown 挂在遮罩上，焦点不在里面就冒泡不上来（旁边那个弹窗靠输入框拿焦点）。
        document.getElementById('preferences-close').focus();
        loadPreferences();
    }

    function closePreferences() {
        preferencesOverlay.classList.add('hidden');
    }

    async function loadPreferences() {
        let items = [];
        try {
            const response = await fetch('/api/preferences');
            if (!response.ok) {
                throw new Error('读不到偏好');
            }
            items = (await response.json()).items || [];
        } catch (error) {
            showError('读不到偏好，请稍后重试');
            closePreferences();
            return;
        }
        renderPreferences(items);
    }

    function renderPreferences(items) {
        preferencesList.innerHTML = '';
        // 删掉最后一条**回到空态**，而不是留一个空列表（§2.3.4）。
        preferencesEmpty.classList.toggle('hidden', items.length > 0);
        items.forEach((item) => preferencesList.appendChild(preferenceRow(item)));
    }

    function preferenceRow(item) {
        const row = document.createElement('div');
        row.className = 'preference-row';

        const body = document.createElement('div');
        body.className = 'preference-body';
        const text = document.createElement('div');
        text.className = 'preference-text';
        // **同行的人那条带上是谁**：只写内容的话，用户会以为那是自己的、然后删错——
        // 与接口回 subject（§2.3.2）是同一个理由，这里是它的另一半。
        text.textContent = item.subject && item.subject !== '本人'
            ? item.subject + '：' + item.content
            : item.content;
        const source = document.createElement('div');
        source.className = 'preference-source';
        source.textContent = PREFERENCE_SOURCE_TEXT[item.source] || item.source;
        body.appendChild(text);
        body.appendChild(source);

        const remove = document.createElement('button');
        remove.className = 'ghost-button preference-delete';
        remove.type = 'button';
        remove.textContent = '删除';
        remove.addEventListener('click', () => deletePreference(item, row));

        row.appendChild(body);
        row.appendChild(remove);
        return row;
    }

    async function deletePreference(item, row) {
        let deleted = false;
        try {
            const response = await fetch('/api/preferences/' + item.id, { method: 'DELETE' });
            deleted = (await response.json()).deleted === true;
        } catch (error) {
            showError('删除失败，请稍后重试');
            return;
        }
        if (!deleted) {
            // 删别人的 / 已经不在了——**不当成成功**（那是后端回 false 的两种情况，
            // 前端分不出来，也不该分）。重拉一遍看真实状态。
            showError('这条没能删掉，可能已经不在了');
            loadPreferences();
            return;
        }
        // **当场生效**：从列表里摘掉那一行，不等刷新（§2.3.4）。
        row.remove();
        if (!preferencesList.children.length) {
            preferencesEmpty.classList.remove('hidden');
        }
    }

    document.getElementById('open-preferences').addEventListener('click', openPreferences);
    document.getElementById('preferences-close').addEventListener('click', closePreferences);
    // 点遮罩本身才算关。用 mousedown 而不是 click：click 的 target 是按下与抬起
    // **共同**的祖先，从面板里按下、拖到遮罩上抬起会误判成点了遮罩（与旁边那个同款）。
    preferencesOverlay.addEventListener('mousedown', (event) => {
        if (event.target === preferencesOverlay) {
            closePreferences();
        }
    });
    preferencesOverlay.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            closePreferences();
        }
    });

    async function adjustCurrentTrip() {
        if (!currentTripData || !currentPayload) {
            showError('请先生成一次旅行计划，再进行修改');
            return;
        }

        const adjustmentPrompt = await askAdjustmentPrompt();
        if (adjustmentPrompt === null) {
            return;   // 取消
        }
        await runTripAdjustment(adjustmentPrompt);
    }

    async function runTripAdjustment(adjustmentPrompt, anchorHint, anchorDay, pinHints, pinDay, moveHotel) {
        if (!currentTripData || !currentPayload) {
            showError('请先生成一次旅行计划，再进行修改');
            return;
        }

        clearError();
        setTripAdjustLoading(true);

        try {
            const body = {
                originalRequest: currentPayload,
                adjustmentPrompt: adjustmentPrompt
            };
            if (anchorHint) {
                body.anchorHint = anchorHint;
                if (Number.isInteger(anchorDay)) {
                    body.anchorDay = anchorDay;
                }
            }
            if (pinHints && pinHints.length) {
                body.pinHints = pinHints;
                if (Number.isInteger(pinDay)) {
                    body.pinDay = pinDay;
                }
                body.pinMoveHotel = moveHotel === true;
            }
            const response = await fetch('/api/trips/adjust', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(body)
            });

            if (!response.ok) {
                const errorData = await response.json().catch(() => null);
                console.error('Trip adjust request failed:', errorData);
                throw new Error(formatErrorMessage(errorData) || '旅行计划调整失败');
            }

            const data = await response.json();
            renderResult(data.tripPlan, data.updatedRequest);
            // 后端写回的整趟锚点回填表单，保证下次「编辑行程」重新收集请求时不丢
            const hotelAnchorInput = document.getElementById('hotelAnchor');
            if (hotelAnchorInput && data.updatedRequest) {
                hotelAnchorInput.value = data.updatedRequest.hotelAnchor || '';
            }
            statusText.textContent = `已按要求完成调整：${adjustmentPrompt}`;
            window.scrollTo({ top: 0, behavior: 'smooth' });
        } catch (error) {
            showError(error.message);
        } finally {
            setTripAdjustLoading(false);
        }
    }

    function renderOverview(data, payload) {
        const attractionCount = (data.days || []).reduce((sum, day) => sum + ((day.attractions || []).length), 0);
        const routeMinutes = (data.days || []).reduce((sum, day) => sum + (day.routePlan?.totalDurationMinutes || 0), 0);
        document.getElementById('overview-days').textContent = `${(data.days || []).length}天`;
        document.getElementById('overview-attractions').textContent = `${attractionCount}个`;
        document.getElementById('overview-budget').textContent = `¥${data.budget?.total || 0}`;
        document.getElementById('overview-weather').textContent = data.weatherInfo?.[0]?.dayWeather || '--';
        document.getElementById('overview-route').textContent = `${routeMinutes}分钟`;
    }

    function buildSummaryText(data, payload) {
        return data.overallSuggestions
            || `${payload.city} ${getTripDays()} 天旅行方案已生成。建议结合天气、预算和住宿偏好灵活调整节奏。`;
    }

    // 票根上的三个字段（FRONTEND_REDESIGN_PLAN 2.4）。票号规则 TRIP-{出发日期}-{目的地城市名}，
    // 不引入城市→三字码映射（后端无此字段）；票据感由打孔线 + 等宽字承担，不依赖 IATA 码。
    function renderTicket(data, payload) {
        const city = payload.city || data.city || '';
        const startDate = (payload.startDate || '').replace(/-/g, '');
        const tripNumber = document.getElementById('ticket-number');
        const originEl = document.getElementById('ticket-origin');
        const cityEl = document.getElementById('ticket-city');
        const coordsEl = document.getElementById('ticket-coords');
        const geoCorner = document.getElementById('geo-corner');
        const days = document.getElementById('ticket-days');
        const total = document.getElementById('ticket-budget');

        if (tripNumber) {
            tripNumber.textContent = startDate ? `TRIP-${startDate}-${city}` : '--';
        }
        if (originEl) {
            // 出发城市后端不接收（TripPlanRequest 无 origin 字段），这里只如实回显委托单上填的值
            const origin = document.getElementById('origin')?.value.trim();
            originEl.textContent = origin || '--';
        }
        if (cityEl) {
            cityEl.textContent = city || '--';
        }

        // 坐标读数取这趟行程首尾两个真实景点的坐标。
        // 反馈单允许写死「31.23°N 121.47°E → 39.90°N 116.40°E」这样的示例值，但那是京沪的坐标，
        // 落在任何别的城市上都成了可见的错误信息；所以改成有坐标才显示、没有就整行隐藏。
        const readings = collectItineraryCoordinates(data);
        if (coordsEl) {
            const text = readings.length > 1 ? `${readings[0]} → ${readings[readings.length - 1]}` : '';
            coordsEl.textContent = text;
            coordsEl.classList.toggle('hidden', !text);
        }
        if (geoCorner) {
            const text = readings.length ? readings[readings.length - 1] : '';
            geoCorner.textContent = text;
            geoCorner.classList.toggle('hidden', !text);
        }

        if (days) {
            const dayCount = (data.days || []).length;
            days.textContent = dayCount ? `${dayCount} DAYS` : '--';
        }
        if (total) {
            total.textContent = `¥${data.budget?.total ?? 0}`;
        }
    }

    // 高德给的坐标是十进制度数，正负号换成 N/S、E/W 更贴测绘图纸的读法
    function formatCoordinate(location) {
        if (!location) {
            return '';
        }
        const lat = Number(location.latitude);
        const lng = Number(location.longitude);
        if (!Number.isFinite(lat) || !Number.isFinite(lng)) {
            return '';
        }
        const latText = `${Math.abs(lat).toFixed(2)}°${lat >= 0 ? 'N' : 'S'}`;
        const lngText = `${Math.abs(lng).toFixed(2)}°${lng >= 0 ? 'E' : 'W'}`;
        return `${latText} ${lngText}`;
    }

    // 按行程顺序收集所有带坐标的景点；首尾两点就是这趟行程的地理跨度
    function collectItineraryCoordinates(data) {
        const readings = [];
        (data?.days || []).forEach(day => {
            (day.attractions || []).forEach(spot => {
                const text = formatCoordinate(spot.location);
                if (text) {
                    readings.push(text);
                }
            });
        });
        return readings;
    }

    function renderPlanningNotes(data, payload) {
        const notes = document.getElementById('planning-notes');
        const summaryMetaGrid = document.getElementById('summary-meta-grid');
        const summaryBadges = document.getElementById('summary-badges');
        const budget = data.budget || {};
        const remainingBudget = budget.remainingBudget ?? ((payload.budget || 0) - (budget.total || 0));
        const routeProvider = (data.days || []).find(day => day.routePlan?.provider?.providerName)?.routePlan?.provider?.providerName;
        const routeMinutes = (data.days || []).reduce((sum, day) => sum + (day.routePlan?.totalDurationMinutes || 0), 0);
        const routeKm = (data.days || []).reduce((sum, day) => sum + (day.routePlan?.totalDistanceKm || 0), 0);
        // 每天的方式**可能不一样**：「智能推荐」那一档（表单默认项，值是空串）就是按天判的，
        // 景点多或者要求慢节奏的那天会给「公共交通 + 短途打车」。
        // 所以不能拿"第一个有方式的那天"冒充整趟——那个串里含"打车"两个字，钱是按里程算的，
        // 卡片却写「公共交通」，会让人以为那天也只花 20（实测 3 天 20/221/20，卡片却显示公共交通）。
        const dayTransportations = [...new Set((data.days || [])
            .map(day => day.transportation)
            .filter(Boolean))];
        const primaryTransportation = dayTransportations.length === 0
            ? (payload.transportation || '智能推荐')
            : dayTransportations.length === 1
                ? dayTransportations[0]
                : `按天调整（${dayTransportations.join('、')}）`;
        const notePairs = [
            { label: '日期', value: `${payload.startDate} 至 ${payload.endDate}`, mono: true },
            { label: '交通方式', value: primaryTransportation },
            { label: '住宿偏好', value: payload.accommodation },
            { label: '偏好', value: payload.preferences.join('、') }
        ];
        const sourceNotes = (data.planningNotes || []).filter(note =>
            note.startsWith('景点来源：') || note.startsWith('天气来源：') || note.startsWith('酒店来源：')
        );
        if (routeProvider) {
            sourceNotes.push(`路线来源：${routeProvider}`);
        }
        if (payload.extraRequirements) {
            notePairs.push({ label: '额外要求', value: payload.extraRequirements });
        }
        // 来源文案自带「景点来源：xxx」形式，拆成标签与值两栏，列表左边缘才对得齐
        sourceNotes.forEach(note => {
            const [label, ...rest] = note.split('：');
            notePairs.push({ label: label || '来源', value: rest.join('：') });
        });
        notes.innerHTML = notePairs.map(pair => `
            <li>
                <span class="note-label">${pair.label}</span>
                <span class="note-value${pair.mono ? ' mono' : ''}">${pair.value}</span>
            </li>
        `).join('');

        summaryBadges.innerHTML = `
            <div class="hero-chip ${remainingBudget < 0 ? 'warn' : 'ok'}">${remainingBudget < 0 ? '预算偏紧' : '预算健康'}</div>
            <div class="hero-chip">${data.loopSummary?.enabled ? `Loop ${data.loopSummary.roundsExecuted || 1}轮` : '单轮规划'}</div>
            <div class="hero-chip">${routeProvider || '规则路线规划'}</div>
        `;

        summaryMetaGrid.innerHTML = `
            <div class="hero-meta-item">
                <span>出行日期</span>
                <strong>${payload.startDate} 至 ${payload.endDate}</strong>
            </div>
            <div class="hero-meta-item">
                <span>交通策略</span>
                <strong>${primaryTransportation}</strong>
            </div>
            <div class="hero-meta-item ${remainingBudget >= 0 ? 'ok' : 'warn'}">
                <span>预算状态</span>
                <strong>${remainingBudget >= 0 ? `预计结余 ${remainingBudget} 元` : `超支 ${Math.abs(remainingBudget)} 元`}</strong>
            </div>
            <div class="hero-meta-item">
                <span>通勤规模</span>
                <strong>${routeMinutes} 分钟 / ${round(routeKm)} 公里</strong>
            </div>
            <div class="hero-meta-item wide text">
                <span>系统判断</span>
                <strong>${data.loopSummary?.stopReason || '当前方案已完成生成，可继续编辑再优化。'}</strong>
            </div>
        `;
    }

    function renderBudget(data, payload) {
        const budget = data.budget || {};
        const ticketCost = budget.totalAttractions || 0;
        const stayCost = budget.totalHotels || 0;
        const foodCost = budget.totalMeals || 0;
        const transportCost = budget.totalTransportation || 0;
        const intercityCost = budget.totalIntercity || 0;
        const estimatedTotal = budget.total || 0;
        const requestedBudget = budget.requestedBudget || payload.budget || 0;
        const remainingBudget = budget.remainingBudget ?? (payload.budget - estimatedTotal);
        const usageRatio = requestedBudget > 0 ? Math.min(estimatedTotal / requestedBudget, 1) : 0;

        const budgetGrid = document.getElementById('budget-grid');
        budgetGrid.innerHTML = `
            <div class="budget-item">
                <span class="budget-item-label">用户预算</span>
                <strong>¥${requestedBudget}</strong>
                <small>本次规划约束的总预算上限</small>
            </div>
            <div class="budget-item">
                <span class="budget-item-label">景点门票</span>
                <strong>¥${ticketCost}</strong>
                <small>门票按景点类别估算后累加，非实时票价</small>
            </div>
            <div class="budget-item">
                <span class="budget-item-label">酒店住宿</span>
                <strong>¥${stayCost}</strong>
                <small>按天数与酒店档位估算累计</small>
            </div>
            <div class="budget-item">
                <span class="budget-item-label">餐饮费用</span>
                <strong>¥${foodCost}</strong>
                <small>早餐 / 午餐 / 晚餐整体估算</small>
            </div>
            <div class="budget-item">
                <span class="budget-item-label">交通费用</span>
                <strong>¥${transportCost}</strong>
                <small>根据路线距离与方式估算</small>
            </div>
            ${intercityCost > 0 ? `
            <div class="budget-item">
                <span class="budget-item-label">往返大交通</span>
                <strong>¥${intercityCost}</strong>
                <small>${budget.intercityEstimated
                    ? `${budget.intercityMode || ''}价格按直线距离估算，非实时票价`
                    : `${budget.intercityMode || ''}高德换乘方案参考价，含两端接驳`}</small>
            </div>` : ''}
            <div class="budget-item${(budget.withinBudget === false || (budget.remainingBudget ?? 0) < 0) ? ' is-over' : ''}">
                <span class="budget-item-label">剩余预算</span>
                <strong>¥${remainingBudget}</strong>
                <small>${remainingBudget >= 0 ? '当前方案仍在预算内' : '当前方案已超出预算'}</small>
            </div>
        `;
        document.getElementById('budget-progress-text').textContent = `¥${estimatedTotal} / ¥${requestedBudget}`;
        const overBudget = budget.withinBudget === false || remainingBudget < 0;
        const progressFillEl = document.getElementById('budget-progress-fill');
        progressFillEl.style.width = `${usageRatio <= 0 ? 0 : Math.max(8, Math.round(usageRatio * 100))}%`;
        progressFillEl.classList.toggle('warn', overBudget);
        document.getElementById('budget-total').textContent = `¥${estimatedTotal}`;
        const status = document.getElementById('budget-status');
        status.className = `budget-status ${overBudget ? 'warn' : 'ok'}`;
        status.textContent = overBudget
            ? `预算偏紧, 当前状态 ${budget.status || 'over_budget'}, 超出 ${Math.abs(remainingBudget)} 元`
            : `预算可控, 当前状态 ${budget.status || 'within_budget'}, 预计结余 ${remainingBudget} 元`;
    }

    async function renderMap(data, payload, dayIndex = 0) {
        currentMapDayIndex = dayIndex;
        updateMapDayTabs(dayIndex);
        updateMapSideMeta(data, dayIndex);
        const mapBox = document.getElementById('map-box');
        const mapShell = ensureMapShell();
        setMapStatus('正在加载真实地图...', false);

        try {
            const config = await fetchMapConfig();
            if (!config.enabled) {
                mapBox.innerHTML = `
                    <div class="map-placeholder">
                        <strong>真实地图待开启</strong>
                        <p>${config.message || '尚未配置高德 Web JS 地图 Key。你补上前端地图 key 后，这里就会显示真实底图、景点点位和每日路线。'}</p>
                    </div>
                `;
                return;
            }

            await loadAmapSdk(config);
            if (!amapMap) {
                amapMap = new AMap.Map(mapShell.canvasId, {
                    resizeEnable: true,
                    zoom: 11,
                    mapStyle: 'amap://styles/whitesmoke'
                });
                amapMap.addControl(new AMap.Scale());
                amapMap.addControl(new AMap.ToolBar({
                    position: {
                        right: '12px',
                        top: '58px'
                    }
                }));
                amapInfoWindow = new AMap.InfoWindow({
                    offset: new AMap.Pixel(0, -24)
                });
            }

            renderDayRouteOnMap(data, payload, dayIndex);
        } catch (error) {
            console.error('Map render failed:', error);
            mapBox.innerHTML = `
                <div class="map-placeholder">
                    <strong>真实地图加载失败</strong>
                    <p>${error.message || '高德地图脚本加载异常，请检查 AMAP_WEB_JS_KEY / AMAP_SECURITY_JS_CODE 配置。'}</p>
                </div>
            `;
        }
    }

    function renderWeather(data, payload) {
        const firstWeather = data.weatherInfo?.[0];
        document.getElementById('weather-condition').textContent = firstWeather
            ? `${firstWeather.dayWeather} / ${firstWeather.nightWeather}`
            : '--';
        document.getElementById('weather-range').textContent = firstWeather
            ? `白天 ${firstWeather.dayTemp}°C · 夜间 ${firstWeather.nightTemp}°C · ${firstWeather.windDirection}${firstWeather.windPower}`
            : '温度范围 --';
        const sourceChip = document.getElementById('weather-source');
        if (firstWeather?.sourceNote) {
            sourceChip.classList.remove('hidden');
            sourceChip.classList.toggle('warn', firstWeather.source !== 'amap-live');
            sourceChip.textContent = firstWeather.source === 'amap-live'
                ? `天气来源：高德真实预报`
                : `天气来源：规则补全`;
        } else {
            sourceChip.classList.add('hidden');
            sourceChip.classList.remove('warn');
            sourceChip.textContent = '';
        }
        const tips = (data.weatherInfo || []).map(item => {
            const sourceText = item.source === 'amap-live' ? '高德' : '补全';
            return `${item.date}: ${item.dayWeather}，${item.dayTemp}°C / ${item.nightTemp}°C · ${sourceText}`;
        });
        document.getElementById('weather-tips').innerHTML = `
            <li>${payload.city}出行期间建议预留弹性时间, 便于按天气微调路线。</li>
            ${tips.map(item => `<li>${item}</li>`).join('')}
        `;
    }

    // 线路色：与 tokens.css「线路色」小节一一对应。JS 读不到 CSS 自定义属性在
    // 高德 Polyline 这种非 DOM 参数上的值，只能在这里重复一次；改 token 时两处要一起改。
    //
    // v4.3 对账：前两支一直是 v3 的值，和 CSS 已经对不上了 ——
    //   第 1 支 #1B4ED8（v3 票据蓝）  ≠ --sea #0B6BA8
    //   第 2 支 #0F7B5F（v3 祖母绿）  ≠ --line-emerald #1E8C57
    // 后果是同一天的地图折线和 DAY 徽章/详情区是两种颜色。这里改成与 tokens 一致。
    const LINE_COLORS = ['#0B6BA8', '#14785A', '#B45309', '#0E7490'];

    // 第 n 天的线路色。三处曾各存一份：tokens 的 --line-* 变量、result.css 里的
    // :nth-child 取模、以及这里。CSS 那份 v4.3 之前一直是错的 —— #day-list 的第一个
    // 子元素是 .day-strip，所以 .day-section:nth-child(4n+1) 永远匹配不到第一天，
    // 详情区整体错开一天（第 1 天的券是海蓝、详情区却是祖母绿）。
    // 现在改为 CSS 不再取模、由这里把 --line-color 内联写进券和详情区，单一来源。
    const lineColor = (index) => LINE_COLORS[index % LINE_COLORS.length];

    // 券上只留 MM-DD：年份在票根的 TRIP NO. 和正下方那天的详情头上都有，
    // 而券宽在主列里只有 60~90px，带年份的 10 位日期从第 5 天起就会被截断
    function stubDate(date) {
        const text = String(date || '');
        return text.length >= 10 ? text.slice(5) : (text || '--');
    }

    // 地铁线路图式当日路线：站点＝口岸/酒店/景点，站间＝路线段的通勤分钟。
    // 数据全部来自 routePlan.steps，没有路线时整条不渲染。
    function buildRouteLine(routeSteps, hotelName, routePlan) {
        const steps = routeSteps || [];
        if (!steps.length) return '';
        const stations = [];
        const segs = [];
        steps.forEach(step => {
            if (!stations.length || stations[stations.length - 1] !== step.fromName) {
                stations.push(step.fromName);
            }
            stations.push(step.toName);
            segs.push(step.durationMinutes || 0);
        });
        const pointName = point => (point && point.name) || '';
        const special = {};
        // 端点自带 point_type：口岸画菱形点、酒店画方点，第 N 天的起点是"前一晚那家酒店"时也要认出来
        const mark = point => {
            const type = point && (point.point_type || point.pointType);
            if (!pointName(point) || !type) return;
            special[pointName(point)] = type === 'station' ? 'station' : 'hotel';
        };
        mark(routePlan && routePlan.startPoint);
        mark(routePlan && routePlan.endPoint);
        if (hotelName) special[hotelName] = 'hotel';
        return `
            <div class="rline" aria-label="当日路线示意图">
                ${stations.map((name, i) => `
                    <div class="rline-node${special[name] ? ' ' + special[name] : ''}">
                        <span class="rline-dot"></span>
                        <span class="rline-name">${name}</span>
                    </div>
                    ${i < stations.length - 1 ? `<div class="rline-seg"><span class="rline-time">${segs[i]}′</span></div>` : ''}
                `).join('')}
            </div>
        `;
    }

    // 酒店卡。返程那天没有住宿（当天直接去口岸），所以 day.hotel 为空时**不能再往下编**——
    // 以前那三格会填上 inferHotelType/inferHotelPrice 和 `4.${index+1}` 的占位值，
    // 看着像那天真有地方住，价格评分还像是查来的。
    // 评分那格的兜底从"按第几天编一个 4.x"改成「暂无」：评分本该来自数据源，
    // 拿不到就老实说没有——编出来的数字会跟真评分混在一起，分不出来。
    function buildHotelBox(day, hotelName, hotelArea) {
        if (!day.hotel) {
            return `
                <div class="hotel-box no-stay">
                    <div class="hotel-name">${hotelName}</div>
                    <div class="hotel-meta">
                        <div>
                            <span class="hotel-meta-label">安排</span>
                            <span class="hotel-meta-value">当天直接前往口岸离城，这一晚不含住宿</span>
                        </div>
                    </div>
                </div>
            `;
        }
        return `
            <div class="hotel-box">
                <div class="hotel-name">${hotelName}</div>
                <div class="hotel-meta">
                    <div>
                        <span class="hotel-meta-label">地址</span>
                        <span class="hotel-meta-value">${hotelArea}</span>
                    </div>
                    <div>
                        <span class="hotel-meta-label">类型</span>
                        <span class="hotel-meta-value">${day.hotel.type || inferHotelType(hotelName)}</span>
                    </div>
                    <div>
                        <span class="hotel-meta-label">价格范围（估算）</span>
                        <span class="hotel-meta-value">${day.hotel.priceRange || inferHotelPrice(hotelName)}</span>
                    </div>
                    <div>
                        <span class="hotel-meta-label">评分</span>
                        <span class="hotel-meta-value">${day.hotel.rating || '暂无'}</span>
                    </div>
                </div>
            </div>
        `;
    }

    function renderDays(data, payload) {
        const dayList = document.getElementById('day-list');
        const days = data.days || [];

        // 联票条：N 张券首尾相接成一整条，只当索引用 —— 点哪张就在下面展开哪天的完整详情。
        // 券数量仍由 days 驱动，循环本身没改，只是把模板拆成「索引条 + 详情」两段。
        const dayStrip = `
            <div class="day-strip" role="tablist" aria-label="按天切换行程详情">
                ${days.map((day, index) => {
                    const names = (day.attractions || []).map(spot => spot.name).filter(Boolean);
                    const preview = names.slice(0, 2).join('·') || '暂无景点安排';
                    const isActive = index === 0;
                    return `
                        <button type="button" class="day-stub${isActive ? ' active' : ''}"
                                data-day-stub="${index}" role="tab" aria-selected="${isActive}"
                                style="--line-color:${lineColor(index)}">
                            <span class="day-stub-head">
                                <span class="day-stub-day">DAY ${(day.dayIndex ?? index) + 1}</span>
                                <span class="day-stub-date">${stubDate(day.date)}</span>
                            </span>
                            <span class="day-stub-body">${preview}</span>
                        </button>
                    `;
                }).join('')}
            </div>
        `;

        dayList.innerHTML = dayStrip + days.map((day, index) => {
            const dateText = day.date;
            const dayAttractions = day.attractions || [];
            // 返程那天没有住宿（那天直接去口岸）。不能退回 accommodation——
            // 那是偏好词（"舒适型酒店"），显示成住宿会让人以为那天还有地方住。
            const isReturnDay = index === days.length - 1 && !day.hotel;
            const hotelName = day.hotel?.name
                || (isReturnDay ? '返程日 · 无住宿' : (day.accommodation || payload.accommodation));
            const hotelArea = day.hotel?.address || (isReturnDay ? '当天离城，不含住宿' : `${payload.city}中心城区`);
            const routePlan = day.routePlan;
            const routeSteps = routePlan?.steps || [];
            const dailyCost = day.estimatedCost || 0;

            return `
                <div class="day-section" style="--line-color:${lineColor(index)}">
                    <div class="day-header" data-day-toggle="${index}">
                        <strong><span class="caret${index === 0 ? ' open' : ''}"></span>第${(day.dayIndex ?? index) + 1}天</strong>
                        <span class="day-date">${dateText}</span>
                    </div>
                    <div class="day-body ${index === 0 ? '' : 'hidden'}" data-day-body="${index}">
                        ${buildRouteLine(routeSteps, hotelName, routePlan)}
                        <div class="meta-box">
                            <div class="meta-row">行程描述: ${day.description || '暂无描述'}</div>
                            <div class="meta-row">交通方式: ${day.transportation || payload.transportation}</div>
                            <div class="meta-row">住宿: ${hotelName}</div>
                            <div class="meta-row">路线来源: ${day.routePlan?.provider?.providerName || '路线规划引擎'}</div>
                            ${renderOptimizationNotes(day.optimizationNotes)}
                            <div class="meta-chip-row">
                                <div class="meta-chip">当日预估 ¥${dailyCost}</div>
                                <div class="meta-chip">通勤 ${routePlan?.totalDurationMinutes || 0} 分钟</div>
                                <div class="meta-chip">路线 ${routePlan?.totalDistanceKm || 0} 公里</div>
                            </div>
                        </div>

                        <div class="spot-grid ${dayAttractions.length === 1 ? 'single' : ''}">
                            ${dayAttractions.map((spot, spotIndex) => `
                                <div class="spot-card">
                                    <div class="spot-stub">
                                        <span class="spot-stub-no">${spotIndex + 1}</span>
                                        <span class="spot-stub-cat">${(spot.category || '').split(';')[0] || 'SPOT'}</span>
                                    </div>
                                    <div class="spot-main">
                                        <div class="spot-head">
                                            ${spot.name}
                                            <span class="spot-price" title="门票价按景点类别估算，不是高德实时票价">¥${spot.ticketPrice || 0}（估算）</span>
                                        </div>
                                        <div class="spot-image" style="background-image: url('${resolveSpotImage(spot, payload.city)}')"></div>
                                        <div class="spot-info">
                                            <div><strong>地址:</strong> ${spot.address || '暂无地址'}</div>
                                            <div><strong>游览时长:</strong> ${spot.visitDuration || 0}分钟</div>
                                            <div><strong>描述:</strong> ${spot.description || '暂无描述'}</div>
                                        </div>
                                    </div>
                                </div>
                            `).join('')}
                        </div>

                        ${buildHotelBox(day, hotelName, hotelArea)}

                        <div class="route-box">
                            <div class="route-head">
                                <strong>当日路线</strong>
                                <span class="route-summary">${routePlan?.summary || '暂未生成路线摘要'}</span>
                            </div>
                            <div class="route-steps">
                                ${routeSteps.length ? routeSteps.map(step => `
                                    <div class="route-step">
                                        <div class="route-step-main">
                                            <span>${step.orderIndex}. ${step.fromName} → ${step.toName}</span>
                                            <span>${step.durationMinutes} 分钟 · ${step.distanceKm} 公里</span>
                                        </div>
                                        <div class="route-step-sub">${step.instruction || `${step.transportationMode} 出行`}</div>
                                    </div>
                                `).join('') : `<div class="route-step"><div class="route-step-sub">暂无路线步骤</div></div>`}
                            </div>
                        </div>

                        <div class="meal-ticket">
                            ${[
                                ['早餐', 'breakfast', `${payload.city}风味早餐, 轻量开启当天行程。`],
                                ['午餐', 'lunch', '在主要景点附近安排简洁午餐, 保证行程节奏。'],
                                ['晚餐', 'dinner', '结合用户偏好, 晚上安排本地特色美食体验。'],
                            ].map(([slot, type, fallback]) => {
                                const cost = findMealCost(day.meals, type);
                                return `
                                    <div class="meal-item">
                                        <span class="meal-slot">${slot}</span>
                                        <span class="meal-text">${findMealDescription(day.meals, type) || fallback}</span>
                                        <span class="meal-price">${cost === null ? '—' : `¥${cost}`}</span>
                                    </div>
                                `;
                            }).join('')}
                        </div>
                    </div>
                </div>
            `;
        }).join('');

        bindDayToggles();
        bindDayStubs();
    }

    function renderExtraRequirements(payload) {
        const extraBox = document.getElementById('extra-box');
        const value = payload?.extraRequirements?.trim() || '';
        if (!value) {
            extraBox.classList.add('hidden');
            extraBox.textContent = '';
            return;
        }
        extraBox.classList.remove('hidden');
        extraBox.innerHTML = `<strong>额外要求参考:</strong> ${value}`;
    }

    function renderOptimizationNotes(notes) {
        if (!notes || !notes.length) {
            return '';
        }
        return `
            <div class="meta-row">
                调整说明: ${notes.join('；')}
            </div>
        `;
    }

    function resolveSpotImage(spot, city) {
        if (spot.imageUrl) {
            return spot.imageUrl;
        }
        return buildSpotPlaceholderImage(spot, city);
    }

    function buildSpotPlaceholderImage(spot, city) {
        const text = `${spot.name || ''} ${spot.category || ''} ${spot.description || ''}`;
        let theme = {
            sky: '#E4F2FB',
            land: '#7FD1A4',
            accent: '#0B6BA8',
            icon: 'landmark',
            label: city || 'TRAVEL'
        };
        if (text.includes('博物馆') || text.includes('人文') || text.includes('历史')) {
            theme = { sky: '#FFF1DC', land: '#F0C9A0', accent: '#B45309', icon: 'museum', label: 'MUSEUM' };
        } else if (text.includes('动物园')) {
            theme = { sky: '#DFF2F0', land: '#79BF74', accent: '#14785A', icon: 'zoo', label: 'NATURE' };
        } else if (text.includes('公园') || text.includes('风景') || text.includes('自然') || text.includes('山')) {
            theme = { sky: '#DFF2EA', land: '#6FBD84', accent: '#16794A', icon: 'park', label: 'PARK' };
        } else if (text.includes('美食') || text.includes('餐饮') || text.includes('小吃')) {
            theme = { sky: '#F7E7D2', land: '#F0A65A', accent: '#B45309', icon: 'food', label: 'FOOD' };
        }
        const title = escapeSvgText(spot.name || city || '旅行景点');
        const svg = `
            <svg xmlns="http://www.w3.org/2000/svg" width="900" height="520" viewBox="0 0 900 520">
                <defs>
                    <linearGradient id="sky" x1="0" x2="1" y1="0" y2="1">
                        <stop offset="0" stop-color="${theme.sky}"/>
                        <stop offset="1" stop-color="#FFF7E8"/>
                    </linearGradient>
                    <linearGradient id="hill" x1="0" x2="1" y1="0" y2="0">
                        <stop offset="0" stop-color="${theme.land}"/>
                        <stop offset="1" stop-color="${theme.accent}"/>
                    </linearGradient>
                </defs>
                <rect width="900" height="520" fill="url(#sky)"/>
                <circle cx="760" cy="95" r="54" fill="#FFC24B" opacity="0.9"/>
                <path d="M0 360 C130 285 230 310 360 250 C520 176 670 260 900 190 L900 520 L0 520 Z" fill="url(#hill)" opacity="0.6"/>
                <path d="M0 420 C170 350 310 400 455 330 C620 248 735 330 900 270 L900 520 L0 520 Z" fill="#CFE4F2" opacity="0.55"/>
                ${buildSpotIcon(theme.icon, theme.accent)}
                <text x="56" y="82" fill="#12303F" font-size="34" font-family="Arial, sans-serif" font-weight="700">${title}</text>
                <text x="58" y="126" fill="#5B7686" font-size="20" font-family="Arial, sans-serif" font-weight="700">${theme.label}</text>
            </svg>
        `;
        return `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`;
    }

    function buildSpotIcon(icon, color) {
        if (icon === 'museum') {
            return `<g transform="translate(560 190)" fill="${color}" opacity="0.9"><polygon points="0,80 140,80 70,15"/><rect x="20" y="92" width="20" height="110"/><rect x="60" y="92" width="20" height="110"/><rect x="100" y="92" width="20" height="110"/><rect x="0" y="214" width="140" height="24"/></g>`;
        }
        if (icon === 'zoo') {
            return `<g transform="translate(585 210)" fill="${color}" opacity="0.9"><circle cx="58" cy="58" r="48"/><circle cx="18" cy="16" r="18"/><circle cx="98" cy="16" r="18"/><circle cx="42" cy="54" r="8" fill="#fff"/><circle cx="74" cy="54" r="8" fill="#fff"/><ellipse cx="58" cy="78" rx="18" ry="12" fill="#fff"/></g>`;
        }
        if (icon === 'food') {
            return `<g transform="translate(585 188)" stroke="${color}" stroke-width="18" stroke-linecap="round" opacity="0.9"><path d="M20 30 L20 210"/><path d="M65 30 L65 210"/><path d="M110 30 C165 80 165 145 110 210"/></g>`;
        }
        return `<g transform="translate(560 190)" fill="${color}" opacity="0.9"><path d="M80 0 L150 190 H10 Z"/><rect x="58" y="120" width="44" height="70" fill="#fff" opacity="0.65"/><circle cx="80" cy="70" r="24" fill="#fff" opacity="0.65"/></g>`;
    }

    function escapeSvgText(value) {
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function bindDayToggles() {
        document.querySelectorAll('[data-day-toggle]').forEach(toggle => {
            toggle.addEventListener('click', () => {
                const index = toggle.dataset.dayToggle;
                const body = document.querySelector(`[data-day-body="${index}"]`);
                body.classList.toggle('hidden');
                // 只切箭头朝向，不改写标题文字——原来这里用 Number(index)+1 重写，
                // 与模板的 (day.dayIndex ?? index)+1 不是同一套算法，日后一旦两者不一致就会显示错天数
                const caret = toggle.querySelector('.caret');
                if (caret) {
                    caret.classList.toggle('open', !body.classList.contains('hidden'));
                }
                // 联票条的选中态跟着详情一起走：从详情头收起时不选中任何一张券
                syncDayStubActive(body.classList.contains('hidden') ? -1 : Number(index));
                if (!body.classList.contains('hidden') && currentTripData && currentPayload) {
                    renderMap(currentTripData, currentPayload, Number(index));
                    updateMapDayTabs(Number(index));
                }
            });
        });
    }

    // 联票条是 index，不是又一套状态：点券、点地图 tab、点详情头，最终都收敛到 openDaySection
    function bindDayStubs() {
        document.querySelectorAll('[data-day-stub]').forEach(stub => {
            stub.addEventListener('click', () => {
                const index = Number(stub.dataset.dayStub);
                openDaySection(index);
                if (currentTripData && currentPayload) {
                    renderMap(currentTripData, currentPayload, index);
                    updateMapDayTabs(index);
                }
            });
        });
    }

    function syncDayStubActive(activeIndex) {
        document.querySelectorAll('[data-day-stub]').forEach(stub => {
            const isActive = Number(stub.dataset.dayStub) === activeIndex;
            stub.classList.toggle('active', isActive);
            stub.setAttribute('aria-selected', String(isActive));
        });
    }

    function renderMapTabs(data) {
        const tabs = document.getElementById('map-day-tabs');
        const days = data.days || [];
        tabs.innerHTML = days.map((day, index) => `
            <button type="button" class="map-day-tab ${index === 0 ? 'active' : ''}" data-map-day-tab="${index}">
                第${(day.dayIndex ?? index) + 1}天
            </button>
        `).join('');

        tabs.querySelectorAll('[data-map-day-tab]').forEach(tab => {
            tab.addEventListener('click', () => {
                const index = Number(tab.dataset.mapDayTab);
                if (currentTripData && currentPayload) {
                    renderMap(currentTripData, currentPayload, index);
                    openDaySection(index);
                }
            });
        });
        updateMapSideMeta(data, 0);
    }

    function updateMapDayTabs(activeIndex) {
        document.querySelectorAll('[data-map-day-tab]').forEach(tab => {
            tab.classList.toggle('active', Number(tab.dataset.mapDayTab) === activeIndex);
        });
    }

    function openDaySection(targetIndex) {
        document.querySelectorAll('[data-day-body]').forEach(body => {
            const index = Number(body.dataset.dayBody);
            const shouldOpen = index === targetIndex;
            body.classList.toggle('hidden', !shouldOpen);
            const caret = document.querySelector(`[data-day-toggle="${index}"] .caret`);
            if (caret) {
                caret.classList.toggle('open', shouldOpen);
            }
        });
        syncDayStubActive(targetIndex);
    }

    function updateMapSideMeta(data, activeIndex) {
        const metaBox = document.getElementById('map-side-meta');
        if (!metaBox) {
            return;
        }
        const day = (data.days || [])[activeIndex];
        const routePlan = day?.routePlan;
        metaBox.innerHTML = `
            <span class="map-meta-pill">第${(day?.dayIndex ?? activeIndex) + 1}天</span>
            <span class="map-meta-pill">${routePlan?.provider?.providerName || '路线规划引擎'}</span>
            <span class="map-meta-pill">${routePlan?.steps?.length || 0}段路线</span>
        `;
    }

    function ensureMapShell() {
        const mapBox = document.getElementById('map-box');
        if (!document.getElementById('amap-canvas')) {
            mapBox.innerHTML = `
                <div id="amap-canvas" class="amap-canvas"></div>
                <div id="map-status-badge" class="map-status-badge">地图准备中</div>
            `;
        }
        return {
            canvasId: 'amap-canvas',
            badge: document.getElementById('map-status-badge')
        };
    }

    async function fetchMapConfig() {
        if (mapConfigCache) {
            return mapConfigCache;
        }
        const response = await fetch('/api/map/config');
        if (!response.ok) {
            throw new Error('地图配置获取失败');
        }
        mapConfigCache = await response.json();
        return mapConfigCache;
    }

    async function loadAmapSdk(config) {
        if (window.AMap) {
            return;
        }
        if (amapSdkPromise) {
            return amapSdkPromise;
        }
        amapSdkPromise = new Promise((resolve, reject) => {
            if (config.securityJsCode) {
                window._AMapSecurityConfig = {
                    securityJsCode: config.securityJsCode
                };
            }
            const script = document.createElement('script');
            script.src = `https://webapi.amap.com/maps?v=2.0&key=${encodeURIComponent(config.jsApiKey)}&plugin=AMap.Scale,AMap.ToolBar`;
            script.async = true;
            script.onload = () => resolve();
            script.onerror = () => reject(new Error('高德地图 JS API 加载失败'));
            document.head.appendChild(script);
        });
        return amapSdkPromise;
    }

    function renderDayRouteOnMap(data, payload, dayIndex) {
        const days = data.days || [];
        const day = days[dayIndex] || days[0];
        if (!day) {
            setMapStatus('暂无可展示路线', true);
            return;
        }

        clearMapOverlays();

        const routePoints = buildRoutePoints(day);
        const markerPoints = buildUniqueMarkerPoints(day);
        if (!routePoints.length || !markerPoints.length) {
            setMapStatus('当前行程缺少有效坐标', true);
            return;
        }

        markerPoints.forEach(point => {
            const marker = new AMap.Marker({
                position: [point.longitude, point.latitude],
                anchor: 'bottom-center',
                content: buildMarkerContent(point)
            });
            marker.on('click', () => {
                amapInfoWindow.setContent(buildMarkerInfo(point, payload));
                amapInfoWindow.open(amapMap, marker.getPosition());
            });
            amapMap.add(marker);
            amapMarkers.push(marker);
        });

        amapPolyline = new AMap.Polyline({
            path: routePoints.map(point => [point.longitude, point.latitude]),
            // 第 n 天的路线用该天的线路色，和上方联票券左缘那条色条对上。
            // 原来是写死的 #5f7dff —— 一个不属于任何 token 的颜色，顺手一并清掉。
            strokeColor: LINE_COLORS[dayIndex % LINE_COLORS.length],
            strokeWeight: 6,
            strokeOpacity: 0.85,
            lineJoin: 'round',
            lineCap: 'round',
            showDir: true
        });
        amapMap.add(amapPolyline);
        amapMap.setFitView([...amapMarkers, amapPolyline], false, [56, 56, 56, 56]);

        const routeMode = day.routePlan?.provider?.providerName || '高德地图路线';
        setMapStatus(`真实地图 · 第${(day.dayIndex ?? dayIndex) + 1}天 · ${routeMode}`, false);
    }

    function clearMapOverlays() {
        amapMarkers.forEach(marker => amapMap.remove(marker));
        amapMarkers = [];
        if (amapPolyline) {
            amapMap.remove(amapPolyline);
            amapPolyline = null;
        }
        if (amapInfoWindow) {
            amapInfoWindow.close();
        }
    }

    function buildRoutePoints(day) {
        const points = [];
        const seen = new Set();
        const routePlan = day.routePlan || {};
        const push = (point) => {
            const location = point && point.location;
            if (!location || !point.name || seen.has(point.name)) {
                return;
            }
            seen.add(point.name);
            points.push({ longitude: location.longitude, latitude: location.latitude });
        };
        // 与后端路线同构：起点（前一晚酒店／到达口岸）→ 各景点 → 终点（当晚酒店／返程口岸）。
        // 后端没给端点时，startPoint 就是第一个景点，push 里的按名去重会避免画出零长度的一段。
        push(routePlan.startPoint);
        (day.attractions || []).forEach(push);
        push(routePlan.endPoint);
        if (points.length < 2) {
            push(day.hotel);
        }
        return points;
    }

    function buildUniqueMarkerPoints(day) {
        const points = [];
        const seen = new Set();
        const hotel = day.hotel;
        if (hotel?.location) {
            points.push({
                id: `hotel-${hotel.name}`,
                name: hotel.name,
                address: hotel.address || '暂无地址',
                longitude: hotel.location.longitude,
                latitude: hotel.location.latitude,
                markerLabel: '住',
                type: 'hotel',
                detail: `当晚住宿 · ${hotel.type || '酒店'} · ${hotel.priceRange || '价格待补充'}`
            });
            seen.add(`${hotel.name}-${hotel.location.longitude}-${hotel.location.latitude}`);
        }
        (day.attractions || []).forEach((spot, index) => {
            if (!spot.location) {
                return;
            }
            const key = `${spot.name}-${spot.location.longitude}-${spot.location.latitude}`;
            if (seen.has(key)) {
                return;
            }
            seen.add(key);
            points.push({
                id: `spot-${index}-${spot.name}`,
                name: spot.name,
                address: spot.address || '暂无地址',
                longitude: spot.location.longitude,
                latitude: spot.location.latitude,
                markerLabel: String(index + 1),
                type: 'attraction',
                detail: `停留约 ${spot.visitDuration || 0} 分钟 · 门票 ¥${spot.ticketPrice || 0}（估算）`
            });
        });
        // 路线端点里"既不是本晚酒店也不是景点"的那些（到达口岸、前一晚那家酒店）：
        // 不补就会在地图上只剩一条没有起点的线，而"今天从昨晚那家出发"正是这条线最想讲的事。
        const routePlan = day.routePlan || {};
        [[routePlan.startPoint, '起', '前一晚住的酒店 · 今天从这里出发'],
         [routePlan.endPoint, '终', '当天收工要回的地点']].forEach(([point, label, detail]) => {
            const type = point && (point.point_type || point.pointType);
            if (!point || !point.location || !type || type === 'attraction') {
                return;
            }
            const key = `${point.name}-${point.location.longitude}-${point.location.latitude}`;
            if (seen.has(key)) {
                return;
            }
            seen.add(key);
            points.push({
                id: `endpoint-${label}-${point.name}`,
                name: point.name,
                address: point.address || '路线端点',
                longitude: point.location.longitude,
                latitude: point.location.latitude,
                markerLabel: type === 'station' ? '站' : label,
                type: type === 'station' ? 'station' : 'endpoint',
                detail: type === 'station' ? '城际大交通的落地站点' : detail
            });
        });
        return points;
    }

    function buildMarkerContent(point) {
        return `
            <div class="map-marker-pin ${point.type}">
                <span>${point.markerLabel}</span>
            </div>
        `;
    }

    function buildMarkerInfo(point, payload) {
        return `
            <div class="map-info">
                <strong>${point.name}</strong>
                <div>${point.address}</div>
                <div>${point.detail}</div>
                <div>${payload.city}</div>
            </div>
        `;
    }

    function setMapStatus(text, warn) {
        const shell = ensureMapShell();
        shell.badge.textContent = text;
        shell.badge.className = `map-status-badge ${warn ? 'warn' : ''}`.trim();
    }

    function setActiveNav(targetId) {
        navItems.forEach(item => {
            item.classList.toggle('active', item.dataset.target === targetId);
        });
    }

    // 餐券右侧的等宽价格。Meal.estimatedCost 缺失时不编一个数字出来，直接显示占位符
    function findMealCost(meals, type) {
        if (!Array.isArray(meals)) {
            return null;
        }
        const meal = meals.find(item => item.type === type);
        const cost = Number(meal?.estimatedCost);
        return Number.isFinite(cost) ? cost : null;
    }

    function findMealDescription(meals, type) {
        if (!Array.isArray(meals)) {
            return '';
        }
        const meal = meals.find(item => item.type === type);
        if (!meal) {
            return '';
        }
        return meal.name && meal.description
            ? `${meal.name} - ${meal.description}`
            : (meal.description || meal.name || '');
    }

    function inferHotelType(name) {
        if (name.includes('高性价比')) {
            return '经济型酒店';
        }
        if (name.includes('品质')) {
            return '品质酒店';
        }
        return '舒适型酒店';
    }

    function inferHotelPrice(name) {
        if (name.includes('高性价比')) {
            return '300-500元';
        }
        if (name.includes('品质')) {
            return '900-1400元';
        }
        return '500-800元';
    }

    function formatErrorMessage(errorData) {
        if (!errorData) {
            return '';
        }
        if (Array.isArray(errorData.details) && errorData.details.length > 0) {
            return `${errorData.message}：${errorData.details.join('；')}`;
        }
        return errorData.message || '';
    }

    /* 刷新后把上一趟接回来，必须放在**整个脚本的最末尾**。
     *
     * 它在函数定义处调用看起来更顺眼，但那里会踩暂时性死区：restoreSession 会走到
     * renderResult，而 renderResult 下面的 renderDays 用了一个 1823 行的
     * `const lineColor`——那时它还没初始化，抛 "Cannot access 'lineColor' before
     * initialization"，页面停在首页、方案压根没渲染出来。
     * 这类错在别处的初始化（250 行那几行）里碰不到，是因为它们不走渲染。
     *
     * 还要**等门禁判完**（auth.js 的 boot()）。直接调的话，未登录时会先闪一下
     * 上一个人的方案、再被挡到登录页——那正是 §2.1.7 不许出现的一幕。
     * 而且没认出来就绝不能 restoreSession：它会 un-hide 结果页、
     * 把一份方案渲染到登录页底下。auth.js 哪天没加载，退回原来的行为。
     */
    (window.travelAuthReady || Promise.resolve(true)).then(function (authenticated) {
        if (authenticated === false) {
            return;
        }
        restoreSession();
    });
