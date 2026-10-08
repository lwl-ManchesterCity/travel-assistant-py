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
    const progressPercent = document.getElementById('progress-percent');
    const backHomeBtn = document.getElementById('back-home-btn');
    const editTripBtn = document.getElementById('edit-trip-btn');
    const agentTraceToggle = document.getElementById('agent-trace-toggle');
    const agentTraceList = document.getElementById('agent-trace-list');
    const tripAdjustOverlay = document.getElementById('trip-adjust-overlay');
    const preferenceGroup = document.getElementById('preference-group');
    const hiddenPreferences = document.getElementById('preferences');
    const departureDateInput = document.getElementById('departureDate');
    const endDateInput = document.getElementById('endDate');
    const daysDisplay = document.getElementById('daysDisplay');
    const navItems = document.querySelectorAll('.nav-item[data-target]');

    let progressTimer = null;
    let currentTripData = null;
    let currentPayload = null;
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

    preferenceGroup.addEventListener('change', syncPreferenceValues);
    departureDateInput.addEventListener('change', syncDateRange);
    endDateInput.addEventListener('change', syncDateRange);
    backHomeBtn.addEventListener('click', () => {
        resultPage.classList.add('hidden');
        landingPage.classList.remove('hidden');
        window.scrollTo({ top: 0, behavior: 'smooth' });
    });
    editTripBtn.addEventListener('click', adjustCurrentTrip);
    navItems.forEach(item => {
        item.addEventListener('click', () => {
            setActiveNav(item.dataset.target);
            if (item.dataset.target === 'agent-trace-section') {
                toggleAgentTraceSection(true);
            }
            document.getElementById(item.dataset.target)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
        });
    });
    agentTraceToggle.addEventListener('click', () => toggleAgentTraceSection());
    document.getElementById('ask-submit').addEventListener('click', askAboutCurrentTrip);
    document.getElementById('ask-confirm-yes').addEventListener('click', confirmAskChange);
    document.getElementById('ask-confirm-no').addEventListener('click', hideAskConfirmation);

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
            renderResult(data, payload);
            setTimeout(() => {
                landingPage.classList.add('hidden');
                resultPage.classList.remove('hidden');
                window.scrollTo({ top: 0, behavior: 'smooth' });
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

    function syncDateFieldDisplay(field) {
        const text = formatDateDisplay(field.input.value);
        field.valueEl.textContent = text || '选择日期';
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

    function startFakeProgress() {
        stopProgress();
        let current = 8;
        updateProgress(current, '正在生成行程计划...');
        progressTimer = setInterval(() => {
            current = Math.min(current + Math.floor(Math.random() * 13) + 4, 90);
            updateProgress(current, current < 45 ? '正在分析目的地与偏好...' : '正在整理每日安排与预算...');
            if (current >= 90) {
                stopProgress();
            }
        }, 520);
    }

    function completeProgress() {
        stopProgress();
        updateProgress(100, '旅行计划已生成完成');
    }

    function stopProgress() {
        if (progressTimer) {
            clearInterval(progressTimer);
            progressTimer = null;
        }
    }

    function updateProgress(value, label) {
        progressFill.style.width = `${value}%`;
        progressPercent.textContent = `${value}%`;
        progressLabel.textContent = label;
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

    function toggleAgentTraceSection(forceOpen = null) {
        const explicitOpen = typeof forceOpen === 'boolean' ? forceOpen : null;
        const shouldOpen = explicitOpen === null
            ? agentTraceList.classList.contains('hidden')
            : explicitOpen;
        agentTraceList.classList.toggle('hidden', !shouldOpen);
        agentTraceToggle.textContent = shouldOpen ? '收起详情' : '展开详情';
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
        renderAgentTraces(data);
        renderDays(data, payload);
        renderMapTabs(data);
        renderMap(data, payload, 0);
        renderExtraRequirements(payload);
        setActiveNav('overview-section');
    }

    async function askAboutCurrentTrip() {
        const question = document.getElementById('ask-question').value.trim();
        const status = document.getElementById('ask-status');
        const answerBox = document.getElementById('ask-answer');
        const button = document.getElementById('ask-submit');
        if (!question) {
            showError('请先输入想追问的内容');
            return;
        }
        if (!currentTripData || !currentPayload) {
            showError('请先生成一次旅行计划，再进行追问');
            return;
        }

        button.disabled = true;
        button.textContent = '追问中...';
        hideAskConfirmation();
        status.textContent = '正在读方案并查数据，约需几秒';
        answerBox.innerHTML = '<div class="ask-loading"><span class="ask-loading-spinner"></span>正在思考...</div>';
        try {
            const response = await fetch('/api/trips/ask', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    question: question,
                    tripPlan: currentTripData,
                    request: currentPayload
                })
            });
            const data = await response.json().catch(() => null);
            if (!response.ok) {
                console.error('Trip ask request failed:', data);
                throw new Error(formatErrorMessage(data) || '追问失败');
            }
            answerBox.textContent = data.answer || '未返回回答';
            renderAgentTraces({agentTraces: data.traces || []});
            toggleAgentTraceSection(true);
            status.textContent = '';
            showAskConfirmation(data);
        } catch (error) {
            answerBox.innerHTML = '';
            status.textContent = '';
            hideAskConfirmation();
            showError(error.message);
        } finally {
            button.disabled = false;
            button.textContent = '追问这份方案';
        }
    }

    let pendingAskChange = null;

    function showAskConfirmation(data) {
        const box = document.getElementById('ask-confirm');
        const text = document.getElementById('ask-confirm-text');
        if (data.intent !== 'need_change' || !data.adjustmentPrompt) {
            hideAskConfirmation();
            return;
        }
        pendingAskChange = {
            adjustmentPrompt: data.adjustmentPrompt,
            anchorHint: data.anchorHint || null,
            anchorDay: Number.isInteger(data.anchorDay) ? data.anchorDay : null
        };
        const where = pendingAskChange.anchorHint
            ? `（位置：${pendingAskChange.anchorHint}${pendingAskChange.anchorDay === null ? '，整趟' : `，第 ${pendingAskChange.anchorDay + 1} 天`}）`
            : '';
        text.textContent = `要按这句话重新生成方案吗${where}：${pendingAskChange.adjustmentPrompt}`;
        box.classList.remove('hidden');
    }

    function hideAskConfirmation() {
        pendingAskChange = null;
        document.getElementById('ask-confirm').classList.add('hidden');
    }

    function applyAskAnchorToPayload(change) {
        if (!change.anchorHint) {
            return;
        }
        if (change.anchorDay === null) {
            currentPayload.hotelAnchor = change.anchorHint;
            document.getElementById('hotelAnchor').value = change.anchorHint;
            return;
        }
        currentPayload.dayHotelAnchors = Object.assign({}, currentPayload.dayHotelAnchors || {});
        currentPayload.dayHotelAnchors[change.anchorDay] = change.anchorHint;
    }

    async function confirmAskChange() {
        const change = pendingAskChange;
        if (!change) {
            return;
        }
        hideAskConfirmation();
        applyAskAnchorToPayload(change);
        await runTripAdjustment(change.adjustmentPrompt);
    }

    async function adjustCurrentTrip() {
        if (!currentTripData || !currentPayload) {
            showError('请先生成一次旅行计划，再进行修改');
            return;
        }

        const adjustmentPrompt = window.prompt(
            '输入你想怎么改，比如：预算改成 2500、节奏轻松一点、酒店换成经济型、不要美食多一点自然风景'
        );
        if (adjustmentPrompt === null) {
            return;
        }
        if (!adjustmentPrompt.trim()) {
            showError('改计划要求不能为空');
            return;
        }
        await runTripAdjustment(adjustmentPrompt.trim());
    }

    async function runTripAdjustment(adjustmentPrompt) {
        if (!currentTripData || !currentPayload) {
            showError('请先生成一次旅行计划，再进行修改');
            return;
        }

        clearError();
        setTripAdjustLoading(true);

        try {
            const response = await fetch('/api/trips/adjust', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    originalRequest: currentPayload,
                    adjustmentPrompt: adjustmentPrompt
                })
            });

            if (!response.ok) {
                const errorData = await response.json().catch(() => null);
                console.error('Trip adjust request failed:', errorData);
                throw new Error(formatErrorMessage(errorData) || '旅行计划调整失败');
            }

            const data = await response.json();
            renderResult(data.tripPlan, data.updatedRequest);
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
        const route = document.getElementById('ticket-route');
        const days = document.getElementById('ticket-days');
        const total = document.getElementById('ticket-budget');

        if (tripNumber) {
            tripNumber.textContent = startDate ? `TRIP-${startDate}-${city}` : '--';
        }
        if (route) {
            // 出发城市后端不接收（TripPlanRequest 无 origin 字段），这里只如实回显委托单上填的值
            const origin = document.getElementById('origin')?.value.trim();
            route.textContent = origin && city ? `${origin} → ${city}` : (city || '--');
        }
        if (days) {
            const dayCount = (data.days || []).length;
            days.textContent = dayCount ? `${dayCount} DAYS` : '--';
        }
        if (total) {
            total.textContent = `¥${data.budget?.total ?? 0}`;
        }
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
        const primaryTransportation = (data.days || []).find(day => day.transportation)?.transportation || payload.transportation || '智能推荐';
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
            <div class="budget-item">
                <span class="budget-item-label">剩余预算</span>
                <strong>¥${remainingBudget}</strong>
                <small>${remainingBudget >= 0 ? '当前方案仍在预算内' : '当前方案已超出预算'}</small>
            </div>
        `;
        document.getElementById('budget-progress-text').textContent = `¥${estimatedTotal} / ¥${requestedBudget}`;
        document.getElementById('budget-progress-fill').style.width = `${usageRatio <= 0 ? 0 : Math.max(8, Math.round(usageRatio * 100))}%`;
        document.getElementById('budget-total').textContent = `¥${estimatedTotal}`;
        const status = document.getElementById('budget-status');
        const overBudget = budget.withinBudget === false || remainingBudget < 0;
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

    function renderAgentTraces(data) {
        const traces = data.agentTraces || [];
        const loopSummary = data.loopSummary;
        const loopIterations = data.loopIterations || [];
        if (!traces.length && !loopIterations.length) {
            agentTraceList.innerHTML = `
                <div class="agent-trace-item">
                    <div class="agent-trace-agent">AgentTrace</div>
                    <div>
                        <div class="agent-trace-action">暂无执行轨迹</div>
                        <div class="agent-trace-meta">当前结果未返回智能体轨迹，可能是旧版本接口响应。</div>
                    </div>
                </div>
            `;
            toggleAgentTraceSection(false);
            return;
        }

        const loopSummaryHtml = loopSummary?.enabled ? `
            <div class="agent-loop-summary">
                <strong>Loop Planning 已启用</strong>
                <span>执行轮次：${loopSummary.roundsExecuted || loopIterations.length || 1}</span>
                <span>最终采用：第${(loopSummary.selectedRound || 1)}轮方案</span>
                <span>停止原因：${loopSummary.stopReason || '已达到当前可接受结果'}</span>
            </div>
        ` : '';

        const loopIterationsHtml = loopIterations.length ? `
            <div class="loop-grid">
                ${loopIterations.map(item => `
                    <details class="loop-item ${item.selectedAsBest ? 'best' : ''}">
                        <summary class="agent-collapse-summary loop-item-top">
                            <div class="loop-item-title">第${item.round || 1}轮循环</div>
                            <div class="loop-badge-row">
                                ${item.selectedAsBest ? '<span class="loop-badge best">最终采用</span>' : ''}
                                <span class="loop-badge">${item.critiqueAction || 'KEEP_CURRENT_PLAN'}</span>
                            </div>
                        </summary>
                        <div class="loop-detail-body">
                            <div class="loop-score">
                                <span class="loop-badge">总分 ${item.reflectionScore ?? '--'}</span>
                                <span class="loop-badge">预算 ${item.budgetSeverity ?? 0}</span>
                                <span class="loop-badge">路线 ${item.routeSeverity ?? 0}</span>
                                <span class="loop-badge">天气 ${item.weatherSeverity ?? 0}</span>
                                <span class="loop-badge">体验 ${item.experienceSeverity ?? 0}</span>
                            </div>
                            <div class="agent-trace-meta"><strong>系统动作:</strong> ${item.critiqueReason || '按当前方向继续规划'}</div>
                            <div class="agent-trace-meta"><strong>执行改动:</strong> ${(item.appliedChanges || []).length ? item.appliedChanges.join('；') : '本轮未做显式条件改写'}</div>
                        </div>
                    </details>
                `).join('')}
            </div>
        ` : '';

        agentTraceList.innerHTML = `
            ${loopSummaryHtml}
            ${loopIterationsHtml}
            ${traces.map((trace, index) => `
            <details class="agent-trace-item">
                <summary class="agent-collapse-summary">
                    <div>
                        <div class="agent-trace-agent">${index + 1}. ${trace.agentName || 'Agent'}</div>
                        <div class="agent-trace-status">${trace.status || 'done'}</div>
                    </div>
                    <div class="agent-trace-action">${trace.action || '执行任务'}</div>
                </summary>
                <div class="agent-detail-body">
                    <div class="agent-trace-meta"><strong>输入:</strong> ${trace.inputSummary || '无'}</div>
                    <div class="agent-trace-meta"><strong>输出:</strong> ${trace.outputSummary || '无'}</div>
                </div>
            </details>
            `).join('')}
        `;
        toggleAgentTraceSection(false);
    }

    function renderDays(data, payload) {
        const dayList = document.getElementById('day-list');
        dayList.innerHTML = data.days.map((day, index) => {
            const dateText = day.date;
            const dayAttractions = day.attractions || [];
            const hotelName = day.hotel?.name || day.accommodation || payload.accommodation;
            const hotelArea = day.hotel?.address || `${payload.city}中心城区`;
            const routePlan = day.routePlan;
            const routeSteps = routePlan?.steps || [];
            const dailyCost = day.estimatedCost || 0;

            return `
                <div class="day-section">
                    <div class="day-header" data-day-toggle="${index}">
                        <strong><span class="caret${index === 0 ? ' open' : ''}"></span>第${(day.dayIndex ?? index) + 1}天</strong>
                        <span class="day-date">${dateText}</span>
                    </div>
                    <div class="day-body ${index === 0 ? '' : 'hidden'}" data-day-body="${index}">
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
                                    <div class="spot-head">${spot.name}</div>
                                    <div class="spot-image" style="background-image: url('${resolveSpotImage(spot, payload.city)}')">
                                        <div class="spot-index">${spotIndex + 1}</div>
                                        <div class="spot-price" title="门票价按景点类别估算，不是高德实时票价">¥${spot.ticketPrice || 0}（估算）</div>
                                    </div>
                                    <div class="spot-info">
                                        <div><strong>地址:</strong> ${spot.address || '暂无地址'}</div>
                                        <div><strong>游览时长:</strong> ${spot.visitDuration || 0}分钟</div>
                                        <div><strong>描述:</strong> ${spot.description || '暂无描述'}</div>
                                    </div>
                                </div>
                            `).join('')}
                        </div>

                        <div class="hotel-box">
                            <div class="hotel-name">${hotelName}</div>
                            <div class="hotel-meta">
                                <div>地址: ${hotelArea}</div>
                                <div>类型: ${day.hotel?.type || inferHotelType(hotelName)}</div>
                                <div>价格范围（估算）: ${day.hotel?.priceRange || inferHotelPrice(hotelName)}</div>
                                <div>评分: ${day.hotel?.rating || `4.${index + 1}`}</div>
                            </div>
                        </div>

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

                        <table class="meal-table">
                            <tr><td>早餐</td><td>${findMealDescription(day.meals, 'breakfast') || `${payload.city}风味早餐, 轻量开启当天行程。`}</td></tr>
                            <tr><td>午餐</td><td>${findMealDescription(day.meals, 'lunch') || '在主要景点附近安排简洁午餐, 保证行程节奏。'}</td></tr>
                            <tr><td>晚餐</td><td>${findMealDescription(day.meals, 'dinner') || '结合用户偏好, 晚上安排本地特色美食体验。'}</td></tr>
                        </table>
                    </div>
                </div>
            `;
        }).join('');

        bindDayToggles();
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
            sky: '#c9e7ff',
            land: '#7fc69d',
            accent: '#4f7df3',
            icon: 'landmark',
            label: city || 'TRAVEL'
        };
        if (text.includes('博物馆') || text.includes('人文') || text.includes('历史')) {
            theme = { sky: '#dbe7ff', land: '#b9a77a', accent: '#6f56d9', icon: 'museum', label: 'MUSEUM' };
        } else if (text.includes('动物园')) {
            theme = { sky: '#d8f2ff', land: '#79bf74', accent: '#3d8f5f', icon: 'zoo', label: 'NATURE' };
        } else if (text.includes('公园') || text.includes('风景') || text.includes('自然') || text.includes('山')) {
            theme = { sky: '#d8f2ff', land: '#6fbd84', accent: '#2e7d54', icon: 'park', label: 'PARK' };
        } else if (text.includes('美食') || text.includes('餐饮') || text.includes('小吃')) {
            theme = { sky: '#ffe8cf', land: '#f0a65a', accent: '#d95757', icon: 'food', label: 'FOOD' };
        }
        const title = escapeSvgText(spot.name || city || '旅行景点');
        const svg = `
            <svg xmlns="http://www.w3.org/2000/svg" width="900" height="520" viewBox="0 0 900 520">
                <defs>
                    <linearGradient id="sky" x1="0" x2="1" y1="0" y2="1">
                        <stop offset="0" stop-color="${theme.sky}"/>
                        <stop offset="1" stop-color="#f8fbff"/>
                    </linearGradient>
                    <linearGradient id="hill" x1="0" x2="1" y1="0" y2="0">
                        <stop offset="0" stop-color="${theme.land}"/>
                        <stop offset="1" stop-color="${theme.accent}"/>
                    </linearGradient>
                </defs>
                <rect width="900" height="520" fill="url(#sky)"/>
                <circle cx="760" cy="95" r="54" fill="#fff3bd" opacity="0.95"/>
                <path d="M0 360 C130 285 230 310 360 250 C520 176 670 260 900 190 L900 520 L0 520 Z" fill="url(#hill)" opacity="0.86"/>
                <path d="M0 420 C170 350 310 400 455 330 C620 248 735 330 900 270 L900 520 L0 520 Z" fill="#263f73" opacity="0.16"/>
                ${buildSpotIcon(theme.icon, theme.accent)}
                <text x="56" y="82" fill="#20304f" font-size="34" font-family="Arial, sans-serif" font-weight="700">${title}</text>
                <text x="58" y="126" fill="#53627f" font-size="20" font-family="Arial, sans-serif" font-weight="700">${theme.label}</text>
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
                if (!body.classList.contains('hidden') && currentTripData && currentPayload) {
                    renderMap(currentTripData, currentPayload, Number(index));
                    updateMapDayTabs(Number(index));
                }
            });
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
            strokeColor: '#5f7dff',
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
        const hotel = day.hotel;
        if (hotel?.location) {
            points.push({
                longitude: hotel.location.longitude,
                latitude: hotel.location.latitude
            });
        }
        (day.attractions || []).forEach(spot => {
            if (spot.location) {
                points.push({
                    longitude: spot.location.longitude,
                    latitude: spot.location.latitude
                });
            }
        });
        if (hotel?.location) {
            points.push({
                longitude: hotel.location.longitude,
                latitude: hotel.location.latitude
            });
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
                markerLabel: 'H',
                type: 'hotel',
                detail: `住宿 · ${hotel.type || '酒店'} · ${hotel.priceRange || '价格待补充'}`
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
