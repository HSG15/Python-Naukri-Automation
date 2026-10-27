// ==UserScript==
// @name         Naukri Engagement Assistant
// @namespace    https://naukri-engagement.local/
// @version      1.2.0
// @description  Boosts Naukri search appearance by simulating genuine human browsing — auto-scrolls job listings, opens JDs, reads them, and keeps your profile active. Install in Tampermonkey, then pin a Naukri tab.
// @author       HSG Automation
// @match        https://www.naukri.com/*
// @grant        none
// @run-at       document-idle
// ==/UserScript==

(function () {
    'use strict';

    // =========================================================
    //  CONFIG — tweak these if needed
    // =========================================================
    const CFG = {
        SLEEP_START_HOUR: 23,
        SLEEP_END_HOUR: 7,
        CYCLE_MIN_MS: 3.5 * 60 * 1000,
        CYCLE_MAX_MS: 9 * 60 * 1000,
        READ_MIN_MS: 28 * 1000,
        READ_MAX_MS: 85 * 1000,
        // Pause bot only if user CLICKED or TYPED on THIS tab within this window.
        // Scrolling, tab-switching, and watching do NOT pause the bot.
        USER_ACTIVE_MS: 60 * 1000,
        SEARCH_PAGES: [
            'https://www.naukri.com/data-engineer-jobs?experience=2',
            'https://www.naukri.com/pyspark-developer-jobs?experience=2',
            'https://www.naukri.com/azure-data-engineer-jobs?experience=2',
            'https://www.naukri.com/etl-developer-jobs?experience=2',
        ],
    };

    // =========================================================
    //  Web Worker-based timer
    //  Browsers throttle main-thread setTimeout in background tabs.
    //  Worker timers are exempt and fire accurately regardless of focus.
    // =========================================================
    const _workerSrc = 'self.onmessage=function(e){var d=e.data;setTimeout(function(){self.postMessage({id:d.id});},d.ms);}';
    const _timerWorker = new Worker(URL.createObjectURL(new Blob([_workerSrc], { type: 'application/javascript' })));
    const _pending = {};
    let _timerId = 0;
    _timerWorker.onmessage = function (e) {
        var cb = _pending[e.data.id];
        if (cb) { delete _pending[e.data.id]; cb(); }
    };

    // =========================================================
    //  Utilities
    // =========================================================
    const rand    = (a, b) => Math.random() * (b - a) + a;
    const randInt = (a, b) => Math.floor(rand(a, b + 1));

    // sleep() goes through the Worker — never throttled in background tabs
    const sleep = ms => new Promise(r => {
        const id = ++_timerId;
        _pending[id] = r;
        _timerWorker.postMessage({ id, ms: Math.max(0, ms | 0) });
    });

    const nowStr = () => new Date().toLocaleTimeString('en-IN', { hour12: false });

    const isNight = () => {
        const h = new Date().getHours();
        return h >= CFG.SLEEP_START_HOUR || h < CFG.SLEEP_END_HOUR;
    };

    const isJobsPage       = () => /naukri\.com\/([a-z-]+-jobs|jobs-in-[a-z-]+)/i.test(location.href);
    const isJobDetailsPage = () => /naukri\.com\/(job-listings-|jd\/)/i.test(location.href);

    // =========================================================
    //  State
    // =========================================================
    const pageLoadTime = Date.now();
    let cycleCount = parseInt(localStorage.getItem('nke_cycle_count') || '0', 10);
    let paused = false;

    // lastUserClick — only real mousedown/keydown on THIS tab resets this.
    // wheel/scroll is excluded so watching the bot scroll doesn't pause it.
    let lastUserClick = 0;
    const isUserActive = () => (Date.now() - lastUserClick) < CFG.USER_ACTIVE_MS;

    ['mousedown', 'keydown', 'touchstart'].forEach(evt =>
        document.addEventListener(evt, () => { lastUserClick = Date.now(); }, { passive: true })
    );

    // ── Daily stats ─────────────────────────────────────────────────
    const _today    = new Date().toISOString().slice(0, 10);
    const _statsKey = 'nke_stats_' + _today;
    let dailyStats  = (function () {
        try {
            const s = JSON.parse(localStorage.getItem(_statsKey) || 'null');
            return (s && s.date === _today) ? s : { date: _today, cycles: 0, activeMs: 0 };
        } catch (_) {
            return { date: _today, cycles: 0, activeMs: 0 };
        }
    })();

    let currentCycleStartTime = null;

    function saveDailyStats() {
        try { localStorage.setItem(_statsKey, JSON.stringify(dailyStats)); } catch (_) { }
    }

    function fmtDuration(ms) {
        const s = Math.round(ms / 1000);
        if (s < 60) return s + 's';
        const m = Math.floor(s / 60), rs = s % 60;
        if (m < 60) return m + 'm ' + (rs < 10 ? '0' : '') + rs + 's';
        const h = Math.floor(m / 60), rm = m % 60;
        return h + 'h ' + rm + 'm';
    }

    function updateTodayLine() {
        const el = document.getElementById('__nke_today');
        if (!el) return;
        let totalMs = dailyStats.activeMs;
        if (currentCycleStartTime) totalMs += (Date.now() - currentCycleStartTime);
        el.textContent = (totalMs === 0 && dailyStats.cycles === 0)
            ? 'Today: no activity yet'
            : 'Today: ' + fmtDuration(totalMs) + ' \u00B7 ' + dailyStats.cycles +
              ' cycle' + (dailyStats.cycles !== 1 ? 's' : '');
    }

    // =========================================================
    //  Status Badge UI
    // =========================================================
    let msgEl = null, subEl = null, cycleEl = null, timeEl = null;

    function buildBadge() {
        const old = document.getElementById('__nke_badge');
        if (old) old.remove();
        const el = document.createElement('div');
        el.id = '__nke_badge';
        Object.assign(el.style, {
            position: 'fixed', bottom: '14px', right: '14px',
            zIndex: '2147483647',
            background: 'rgba(8, 12, 28, 0.93)',
            backdropFilter: 'blur(14px)',
            border: '1px solid rgba(100, 160, 255, 0.18)',
            borderRadius: '12px', padding: '10px 16px', minWidth: '210px',
            font: '12px/1.65 "Inter", ui-monospace, monospace',
            boxShadow: '0 8px 32px rgba(0,0,0,0.55)',
            cursor: 'pointer', userSelect: 'none',
            transition: 'opacity 0.25s', opacity: '0.88',
        });
        el.innerHTML = [
            '<div style="display:flex;align-items:center;gap:7px;margin-bottom:5px">',
            '<span id="__nke_icon" style="font-size:14px">&#9654;</span>',
            '<span style="color:#c8d8f8;font-weight:700;letter-spacing:.6px">NKE</span>',
            '<span style="color:#2a3a55;margin-left:auto;font-size:9px;text-transform:uppercase">Naukri Engage</span>',
            '</div>',
            '<div id="__nke_msg" style="color:#7aabdd;font-size:11.5px;font-weight:500">Initialising...</div>',
            '<div id="__nke_sub" style="color:#2c4060;font-size:10px;margin-top:1px"></div>',
            '<div id="__nke_today" style="color:#4ade80;font-size:10.5px;font-weight:600;margin-top:4px;min-height:14px">Today: \u2014</div>',
            '<div style="display:flex;gap:10px;margin-top:6px;padding-top:6px;border-top:1px solid rgba(255,255,255,0.05);font-size:10px;color:#304050">',
            '<span id="__nke_cycle">cycle 0</span>',
            '<span style="margin-left:auto" id="__nke_time"></span>',
            '</div>',
        ].join('');
        el.addEventListener('click', e => {
            e.stopPropagation();
            paused = !paused;
            if (paused) setStatus('\u23F8', 'Paused manually', 'Click to resume');
            else        setStatus('\u25B6', 'Resumed', '');
        });
        el.addEventListener('mouseenter', () => { el.style.opacity = '1'; });
        el.addEventListener('mouseleave', () => { el.style.opacity = '0.88'; });
        (document.body || document.documentElement).appendChild(el);
        msgEl   = document.getElementById('__nke_msg');
        subEl   = document.getElementById('__nke_sub');
        cycleEl = document.getElementById('__nke_cycle');
        timeEl  = document.getElementById('__nke_time');
        updateTodayLine();
        return el;
    }

    function setStatus(icon, msg, sub) {
        try {
            const iconEl = document.getElementById('__nke_icon');
            if (iconEl)  iconEl.textContent  = icon;
            if (msgEl)   msgEl.textContent   = msg;
            if (subEl)   subEl.textContent   = (sub !== undefined) ? sub : '';
            if (cycleEl) cycleEl.textContent = 'cycle ' + cycleCount;
            if (timeEl)  timeEl.textContent  = nowStr();
        } catch (_) { }
    }

    setInterval(() => {
        if (timeEl) timeEl.textContent = nowStr();
        updateTodayLine();
    }, 1000);

    // =========================================================
    //  Smooth scroll — easeInOutCubic
    //  Skips animation when tab is hidden so async chain is never stalled.
    // =========================================================
    function smoothScroll(container, targetY, durationMs) {
        if (document.hidden) {
            try { container.scrollTop = targetY; } catch (_) { }
            return Promise.resolve();
        }
        return new Promise(function (resolve) {
            const startY = container.scrollTop;
            const delta  = targetY - startY;
            if (Math.abs(delta) < 2) return resolve();
            const t0 = performance.now();
            (function step() {
                if (document.hidden) {
                    try { container.scrollTop = targetY; } catch (_) { }
                    return resolve();
                }
                const p    = Math.min((performance.now() - t0) / durationMs, 1);
                const ease = p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2;
                container.scrollTop = startY + delta * ease;
                if (p < 1) setTimeout(step, 20);
                else resolve();
            })();
        });
    }

    // =========================================================
    //  Real pointer + mouse event dispatch (avoids .click())
    // =========================================================
    function humanClick(el) {
        if (!el) return;
        const r  = el.getBoundingClientRect();
        const cx = r.left + r.width  / 2 + rand(-4, 4);
        const cy = r.top  + r.height / 2 + rand(-3, 3);
        const opts = { view: window, bubbles: true, cancelable: true, clientX: cx, clientY: cy };
        ['pointerover','mouseover','pointerenter','mouseenter',
         'pointermove','mousemove',
         'pointerdown','mousedown',
         'pointerup','mouseup','click'].forEach(type => el.dispatchEvent(new MouseEvent(type, opts)));
    }

    // =========================================================
    //  DOM helpers
    // =========================================================
    // waitFor uses Worker-backed sleep for its timeout — fires even in bg tabs
    function waitFor(selector, timeoutMs) {
        timeoutMs = timeoutMs || 8000;
        const found = document.querySelector(selector);
        if (found) return Promise.resolve(found);
        return new Promise(function (resolve) {
            let settled = false;
            const obs = new MutationObserver(function () {
                const el = document.querySelector(selector);
                if (el && !settled) { settled = true; obs.disconnect(); resolve(el); }
            });
            obs.observe(document.body || document.documentElement, { childList: true, subtree: true });
            sleep(timeoutMs).then(function () {
                if (!settled) { settled = true; obs.disconnect(); resolve(null); }
            });
        });
    }

    function getJobCards() {
        const selectors = [
            '.srp-jobtuple-wrapper',
            '.list-tabs-container .cust-job-tuple',
            'article[data-job-id]',
            '.jobTupleHeader',
            '.job-tuple-wrapper',
        ];
        for (const sel of selectors) {
            const cards = Array.from(document.querySelectorAll(sel));
            if (cards.length >= 2) return cards;
        }
        return [];
    }

    function getTitleLink(card) {
        const selectors = ['a.title', '.title a', 'a[href*="job-listings-"]', 'h2 a', '.jobTitle a'];
        for (const sel of selectors) {
            const a = card.querySelector(sel);
            if (a && a.href) return a;
        }
        return card.querySelector('a[href]');
    }

    // =========================================================
    //  Core browsing cycle
    //  Runs regardless of which tab is focused.
    //  Only pauses if user is actively clicking/typing on THIS tab.
    // =========================================================
    async function runCycle() {
        if (paused) {
            setStatus('\u23F8', 'Paused', 'Click badge to resume');
            return;
        }
        if (isNight()) {
            setStatus('\uD83C\uDF19', 'Night mode \u2014 sleeping', 'Resumes after ' + CFG.SLEEP_END_HOUR + ':00 AM');
            return;
        }
        if (isUserActive()) {
            const secsLeft = Math.ceil((CFG.USER_ACTIVE_MS - (Date.now() - lastUserClick)) / 1000);
            setStatus('\uD83D\uDC64', 'You\'re active here \u2014 paused', 'Resumes in ~' + secsLeft + 's');
            return;
        }

        cycleCount++;
        const cycleStart = Date.now();
        currentCycleStartTime = cycleStart;
        const targetPage = CFG.SEARCH_PAGES[cycleCount % CFG.SEARCH_PAGES.length];
        const keyword    = targetPage.split('/').pop().split('?')[0];

        if (!isJobsPage()) {
            setStatus('\uD83D\uDD00', 'Going to job search...', keyword);
            await sleep(rand(800, 2000));
            currentCycleStartTime = null;
            location.href = targetPage;
            return;
        }

        if (cycleCount % 6 === 0) {
            setStatus('\uD83D\uDD00', 'Switching keyword...', keyword);
            await sleep(rand(500, 1200));
            currentCycleStartTime = null;
            location.href = targetPage;
            return;
        }

        // Scroll job list (instant if tab hidden)
        const scrollTarget = randInt(300, 950);
        setStatus('\uD83D\uDCDC', 'Browsing job list...', 'scrolling to ' + scrollTarget + 'px');
        await smoothScroll(document.documentElement, scrollTarget, rand(2200, 5000));
        await sleep(rand(1000, 2500));

        const allCards = getJobCards();
        const cards    = allCards.filter(c => {
            const text = (c.innerText || '').toLowerCase();
            return !text.includes('apply on company site') && !text.includes('external apply');
        });

        if (cards.length === 0) {
            setStatus('\u26A0', 'No suitable job cards detected', 'Will retry next cycle');
            currentCycleStartTime = null;
            return;
        }

        const idx  = randInt(0, Math.min(cards.length - 1, 9));
        const card = cards[idx];
        if (!document.hidden) card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        await sleep(rand(500, 1200));

        const titleLink = getTitleLink(card);
        if (!titleLink) {
            setStatus('\u26A0', 'Title link not found', 'card ' + (idx + 1));
            currentCycleStartTime = null;
            return;
        }

        if (!titleLink.href.includes('naukri.com')) {
            setStatus('\u26A0', 'Skipping external job link', 'Will retry next cycle');
            await sleep(1500);
            currentCycleStartTime = null;
            return;
        }

        setStatus('\uD83D\uDCC4', 'Opening job ' + (idx + 1) + ' of ' + cards.length, '');
        titleLink.removeAttribute('target');
        titleLink.setAttribute('target', '_self');
        humanClick(titleLink);

        // waitFor now uses Worker-backed timeout — fires even when tab is hidden
        const panel = await waitFor('.detail-view-container, .jd-pane, [class*="jobDetail"], [class*="detailView"]', 8000);
        if (!panel) {
            if (titleLink && titleLink.href) {
                console.log('[NKE] Click did not open panel, forcing navigation:', titleLink.href);
                location.href = titleLink.href;
            }
            await sleep(rand(2000, 4000));
            return;
        }
        await sleep(rand(1500, 3000));

        const readMs  = rand(CFG.READ_MIN_MS, CFG.READ_MAX_MS);
        const readSec = Math.round(readMs / 1000);
        setStatus('\uD83D\uDCD6', 'Reading JD \u2014 ~' + readSec + 's', 'job ' + (idx + 1));

        await sleep(rand(2500, 5000));
        await smoothScroll(panel, randInt(180, 480), rand(3000, 6000));
        await sleep(rand(3000, 7000));
        await smoothScroll(panel, randInt(0, 80), rand(1500, 3000));

        const alreadySpent = 14000;
        await sleep(Math.max(0, readMs - alreadySpent));

        dailyStats.cycles++;
        dailyStats.activeMs += (Date.now() - cycleStart);
        currentCycleStartTime = null;
        saveDailyStats();
        updateTodayLine();
        setStatus('\u2705', 'JD read \u2014 job ' + (idx + 1), 'cycle ' + cycleCount + ' complete');
    }

    // =========================================================
    //  Job Details Full-Page Reader
    // =========================================================
    async function runJobDetailsReader() {
        setStatus('\uD83D\uDCD6', 'Reading Job Details...', 'Initialising...');
        currentCycleStartTime = pageLoadTime;
        await sleep(rand(3000, 5000));

        const readMs  = rand(CFG.READ_MIN_MS, CFG.READ_MAX_MS);
        const readSec = Math.round(readMs / 1000);
        setStatus('\uD83D\uDCD6', 'Reading JD \u2014 ~' + readSec + 's', 'Scrolling down...');

        const scrollTarget = randInt(400, 1100);
        await smoothScroll(document.documentElement, scrollTarget, rand(4000, 8000));
        await sleep(rand(3000, 6000));

        const scrollTarget2 = Math.min(scrollTarget + randInt(200, 600), document.documentElement.scrollHeight - 500);
        if (scrollTarget2 > scrollTarget) {
            await smoothScroll(document.documentElement, scrollTarget2, rand(3000, 6000));
            await sleep(rand(3000, 6000));
        }

        await smoothScroll(document.documentElement, Math.max(0, scrollTarget2 - randInt(300, 500)), rand(2500, 5000));

        const spent = Date.now() - pageLoadTime;
        if (readMs > spent) await sleep(readMs - spent);

        dailyStats.cycles++;
        dailyStats.activeMs += (Date.now() - pageLoadTime);
        currentCycleStartTime = null;
        saveDailyStats();
        updateTodayLine();

        setStatus('\u2705', 'Finished reading', 'Returning to search...');
        await sleep(rand(1500, 3000));

        if (window.history.length > 1) window.history.back();
        else location.href = CFG.SEARCH_PAGES[0];
    }

    // =========================================================
    //  Main loop
    // =========================================================
    async function mainLoop() {
        await sleep(rand(3000, 6000));

        while (true) {
            const nextCycleTime = parseInt(localStorage.getItem('nke_next_cycle') || '0', 10);
            const now           = Date.now();

            if (nextCycleTime > now && !paused && !isNight()) {
                const remaining = nextCycleTime - now;
                const mins      = Math.ceil(remaining / 60000);
                setStatus('\u23F3', 'Next cycle in ~' + mins + 'm', nowStr());
                await sleep(Math.min(remaining, 10000));
                continue;
            }

            try {
                cycleCount = parseInt(localStorage.getItem('nke_cycle_count') || '0', 10) + 1;
                localStorage.setItem('nke_cycle_count', String(cycleCount));
                await runCycle();
            } catch (err) {
                setStatus('\u26A0', 'Error in cycle', String(err).slice(0, 50));
                console.warn('[NKE] Cycle error:', err);
            }

            let delay;
            if (paused || isUserActive()) {
                delay = 25000;
            } else if (isNight()) {
                delay = 25 * 60 * 1000;
            } else {
                delay = rand(CFG.CYCLE_MIN_MS, CFG.CYCLE_MAX_MS);
            }

            localStorage.setItem('nke_next_cycle', String(Date.now() + delay));

            let targetTime = Date.now() + delay;
            while (Date.now() < targetTime && !paused && !isNight()) {
                if (isUserActive()) {
                    const secsLeft = Math.ceil((CFG.USER_ACTIVE_MS - (Date.now() - lastUserClick)) / 1000);
                    setStatus('\uD83D\uDC64', 'You\'re active here \u2014 paused', 'Resumes in ~' + secsLeft + 's');
                    await sleep(5000);
                    targetTime = Date.now() + 5000;
                    continue;
                }
                const mins = Math.ceil((targetTime - Date.now()) / 60000);
                setStatus('\u23F3', 'Next cycle in ~' + mins + 'm', nowStr());
                await sleep(Math.min(targetTime - Date.now(), 10000));
            }
        }
    }

    // =========================================================
    //  Re-attach badge after SPA navigation
    // =========================================================
    let _rebuildScheduled = false;
    function scheduleBadgeRebuild() {
        if (_rebuildScheduled) return;
        _rebuildScheduled = true;
        setTimeout(function () {
            _rebuildScheduled = false;
            if (!document.getElementById('__nke_badge')) {
                buildBadge();
                msgEl   = document.getElementById('__nke_msg');
                subEl   = document.getElementById('__nke_sub');
                cycleEl = document.getElementById('__nke_cycle');
                timeEl  = document.getElementById('__nke_time');
                updateTodayLine();
            }
        }, 600);
    }

    new MutationObserver(function () {
        if (!document.getElementById('__nke_badge')) scheduleBadgeRebuild();
    }).observe(document.documentElement, { childList: true, subtree: true });

    setInterval(function () {
        if (!document.getElementById('__nke_badge')) {
            console.log('[NKE] Badge missing \u2014 re-attaching...');
            scheduleBadgeRebuild();
        }
    }, 1000);

    // =========================================================
    //  Boot
    // =========================================================
    function boot() {
        if (/nlogin|checkout|payment/i.test(location.pathname)) {
            setTimeout(function () { location.href = CFG.SEARCH_PAGES[0]; }, 2000);
            return;
        }
        buildBadge();

        if (isJobDetailsPage()) {
            setStatus('\uD83D\uDCD6', 'Job Details Page', 'Preparing reader...');
            console.log('[NKE] Naukri Engagement Assistant v1.2.0 \u2014 Reading Job Details');
            runJobDetailsReader();
        } else {
            setStatus('\u25B6', 'Engagement assistant active', 'Starting loop...');
            console.log('[NKE] Naukri Engagement Assistant v1.2.0 started');
            mainLoop();
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }

})();
