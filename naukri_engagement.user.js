// ==UserScript==
// @name         Naukri Engagement Assistant
// @namespace    https://naukri-engagement.local/
// @version      1.0.0
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
        // Hours when the assistant sleeps (no unnatural overnight activity)
        SLEEP_START_HOUR : 23,              // 11 PM
        SLEEP_END_HOUR   : 7,               // 7 AM

        // Delay between full browsing cycles (ms) — randomised each time
        CYCLE_MIN_MS : 3.5 * 60 * 1000,    // 3.5 min
        CYCLE_MAX_MS : 9   * 60 * 1000,    // 9 min

        // How long to "read" an open JD (ms)
        READ_MIN_MS : 28 * 1000,            // 28 sec
        READ_MAX_MS : 85 * 1000,            // 85 sec

        // Don't auto-browse if user has been active on THIS page within this window
        USER_IDLE_MS : 90 * 1000,           // 90 seconds

        // Job search pages to cycle through (your actual keywords)
        SEARCH_PAGES : [
            'https://www.naukri.com/data-engineer-jobs',
            'https://www.naukri.com/pyspark-developer-jobs',
            'https://www.naukri.com/azure-data-engineer-jobs',
            'https://www.naukri.com/aws-data-engineer-jobs',
            'https://www.naukri.com/etl-developer-jobs',
        ],
    };

    // =========================================================
    //  State
    // =========================================================
    const pageLoadTime = Date.now();
    let cycleCount     = parseInt(localStorage.getItem('nke_cycle_count') || '0', 10);
    let lastUserAction = Date.now();
    let paused         = false;
    let statusEl       = null;
    let msgEl          = null;
    let subEl          = null;
    let cycleEl        = null;
    let timeEl         = null;

    // ── Daily stats (persisted in localStorage) ─────────────────────
    const _today    = new Date().toISOString().slice(0, 10);
    const _statsKey = 'nke_stats_' + _today;
    let   dailyStats = (function () {
        try {
            var s = JSON.parse(localStorage.getItem(_statsKey) || 'null');
            return (s && s.date === _today) ? s : { date: _today, cycles: 0, activeMs: 0 };
        } catch (_) {
            return { date: _today, cycles: 0, activeMs: 0 };
        }
    })();

    // Track user interactions on this page
    ['mousemove', 'mousedown', 'keydown', 'touchstart', 'wheel', 'scroll']
        .forEach(e => document.addEventListener(e, () => { lastUserAction = Date.now(); }, { passive: true }));

    // =========================================================
    //  Utilities
    // =========================================================
    const rand    = (a, b) => Math.random() * (b - a) + a;
    const randInt = (a, b) => Math.floor(rand(a, b + 1));
    const sleep   = ms => new Promise(r => setTimeout(r, ms));
    const nowStr  = () => new Date().toLocaleTimeString('en-IN', { hour12: false });

    const isNight          = () => { const h = new Date().getHours(); return h >= CFG.SLEEP_START_HOUR || h < CFG.SLEEP_END_HOUR; };
    const isIdle           = () => (Date.now() - lastUserAction) > CFG.USER_IDLE_MS;
    const isJobsPage       = () => /naukri\.com\/([a-z-]+-jobs|jobs-in-[a-z-]+)/i.test(location.href);
    const isJobDetailsPage = () => /naukri\.com\/(job-listings-|jd\/)/i.test(location.href);

    // ── Daily stats helpers ─────────────────────────────────────────
    function fmtDuration(ms) {
        var s = Math.round(ms / 1000);
        if (s < 60)  return s + 's';
        var m = Math.floor(s / 60), rs = s % 60;
        if (m < 60)  return m + 'm ' + (rs < 10 ? '0' : '') + rs + 's';
        var h = Math.floor(m / 60), rm = m % 60;
        return h + 'h ' + rm + 'm';
    }

    function saveDailyStats() {
        try { localStorage.setItem(_statsKey, JSON.stringify(dailyStats)); } catch (_) {}
    }

    function updateTodayLine() {
        var el = document.getElementById('__nke_today');
        if (!el) return;
        if (dailyStats.activeMs === 0 && dailyStats.cycles === 0) {
            el.textContent = 'Today: no activity yet';
        } else {
            el.textContent = 'Today: ' + fmtDuration(dailyStats.activeMs)
                + ' \u00B7 ' + dailyStats.cycles
                + ' cycle' + (dailyStats.cycles !== 1 ? 's' : '');
        }
    }

    // =========================================================
    //  Status Badge UI
    // =========================================================
    function buildBadge() {
        const old = document.getElementById('__nke_badge');
        if (old) old.remove();

        const el = document.createElement('div');
        el.id = '__nke_badge';
        Object.assign(el.style, {
            position       : 'fixed',
            bottom         : '14px',
            right          : '14px',
            zIndex         : '2147483647',
            background     : 'rgba(8, 12, 28, 0.93)',
            backdropFilter : 'blur(14px)',
            border         : '1px solid rgba(100, 160, 255, 0.18)',
            borderRadius   : '12px',
            padding        : '10px 16px',
            minWidth       : '210px',
            font           : '12px/1.65 "Inter", ui-monospace, monospace',
            boxShadow      : '0 8px 32px rgba(0,0,0,0.55)',
            cursor         : 'pointer',
            userSelect     : 'none',
            transition     : 'opacity 0.25s',
            opacity        : '0.88',
        });

        el.innerHTML = [
            '<div style="display:flex;align-items:center;gap:7px;margin-bottom:5px">',
                '<span id="__nke_icon" style="font-size:14px">&#9654;</span>',
                '<span style="color:#c8d8f8;font-weight:700;letter-spacing:.6px">NKE</span>',
                '<span style="color:#2a3a55;margin-left:auto;font-size:9px;text-transform:uppercase">Naukri Engage</span>',
            '</div>',
            '<div id="__nke_msg" style="color:#7aabdd;font-size:11.5px;font-weight:500">Initialising...</div>',
            '<div id="__nke_sub" style="color:#2c4060;font-size:10px;margin-top:1px"></div>',
        '<div id="__nke_today" style="color:#4ade80;font-size:10.5px;font-weight:600;margin-top:4px;min-height:14px">Today: —</div>',
            '<div style="display:flex;gap:10px;margin-top:6px;padding-top:6px;border-top:1px solid rgba(255,255,255,0.05);font-size:10px;color:#304050">',
                '<span id="__nke_cycle">cycle 0</span>',
                '<span style="margin-left:auto" id="__nke_time"></span>',
            '</div>',
        ].join('');

        el.addEventListener('click', () => {
            paused = !paused;
            if (paused) setStatus('\u23F8', 'Paused manually', 'Click to resume');
            else        setStatus('\u25B6', 'Resumed', '');
        });
        el.addEventListener('mouseenter', () => { el.style.opacity = '1'; });
        el.addEventListener('mouseleave', () => { el.style.opacity = '0.88'; });

        document.body.appendChild(el);
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
        } catch (_) {}
    }

    setInterval(() => { if (timeEl) timeEl.textContent = nowStr(); }, 30000);

    // =========================================================
    //  Smooth scroll — easeInOutCubic
    // =========================================================
    function smoothScroll(container, targetY, durationMs) {
        return new Promise(function(resolve) {
            const startY = container.scrollTop;
            const delta  = targetY - startY;
            if (Math.abs(delta) < 2) return resolve();
            const t0 = performance.now();
            (function step(t) {
                const p    = Math.min((t - t0) / durationMs, 1);
                const ease = p < 0.5 ? 4*p*p*p : 1 - Math.pow(-2*p+2, 3)/2;
                container.scrollTop = startY + delta * ease;
                if (p < 1) requestAnimationFrame(step);
                else resolve();
            })(performance.now());
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
         'pointerup','mouseup','click'].forEach(function(type) {
            el.dispatchEvent(new MouseEvent(type, opts));
        });
    }

    // =========================================================
    //  DOM helpers
    // =========================================================
    function waitFor(selector, timeoutMs) {
        timeoutMs = timeoutMs || 6000;
        var found = document.querySelector(selector);
        if (found) return Promise.resolve(found);
        return new Promise(function(resolve) {
            var obs = new MutationObserver(function() {
                var el = document.querySelector(selector);
                if (el) { obs.disconnect(); resolve(el); }
            });
            obs.observe(document.body, { childList: true, subtree: true });
            setTimeout(function() { obs.disconnect(); resolve(null); }, timeoutMs);
        });
    }

    function getJobCards() {
        var selectors = [
            '.srp-jobtuple-wrapper',
            '.list-tabs-container .cust-job-tuple',
            'article[data-job-id]',
            '.jobTupleHeader',
            '.job-tuple-wrapper',
        ];
        for (var i = 0; i < selectors.length; i++) {
            var cards = Array.from(document.querySelectorAll(selectors[i]));
            if (cards.length >= 2) return cards;
        }
        return [];
    }

    function getTitleLink(card) {
        var selectors = ['a.title', '.title a', 'a[href*="job-listings-"]', 'h2 a', '.jobTitle a'];
        for (var i = 0; i < selectors.length; i++) {
            var a = card.querySelector(selectors[i]);
            if (a && a.href) return a;
        }
        return card.querySelector('a[href]');
    }

    function getDetailPanel() {
        var selectors = ['.detail-view-container', '.jd-pane', '[class*="jobDetail"]', '.job-desc-container', '[class*="detailView"]'];
        for (var i = 0; i < selectors.length; i++) {
            var el = document.querySelector(selectors[i]);
            if (el) return el;
        }
        return null;
    }

    // =========================================================
    //  Core browsing cycle
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
        if (!isIdle()) {
            setStatus('\uD83D\uDC64', 'You\'re browsing \u2014 paused', 'Resumes after ' + Math.round(CFG.USER_IDLE_MS/1000) + 's idle');
            return;
        }

        cycleCount++;
        var cycleStart  = Date.now();   // track active time for this cycle
        var targetPage = CFG.SEARCH_PAGES[cycleCount % CFG.SEARCH_PAGES.length];
        var keyword    = targetPage.split('/').pop();

        // Step 1: Make sure we're on a jobs listing page
        if (!isJobsPage()) {
            setStatus('\uD83D\uDD00', 'Going to job search...', keyword);
            await sleep(rand(800, 2000));
            location.href = targetPage;
            return;
        }

        // Every 6th cycle: switch search keyword for variety signal
        if (cycleCount % 6 === 0) {
            setStatus('\uD83D\uDD00', 'Switching keyword for variety...', keyword);
            await sleep(rand(500, 1200));
            location.href = targetPage;
            return;
        }

        // Step 2: Scroll job list
        var scrollTarget = randInt(300, 950);
        setStatus('\uD83D\uDCDC', 'Browsing job list...', 'scrolling to ' + scrollTarget + 'px');
        await smoothScroll(document.documentElement, scrollTarget, rand(2200, 5000));
        await sleep(rand(1000, 2500));

        // Step 3: Pick a random job card (top 10 only, weighted)
        var cards = getJobCards();
        if (cards.length === 0) {
            setStatus('\u26A0', 'No job cards detected', 'Will retry next cycle');
            return;
        }
        var idx  = randInt(0, Math.min(cards.length - 1, 9));
        var card = cards[idx];
        card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        await sleep(rand(500, 1200));

        var titleLink = getTitleLink(card);
        if (!titleLink) {
            setStatus('\u26A0', 'Title link not found', 'card ' + (idx + 1));
            return;
        }

        // Step 4: Click the job (forcing same-tab navigation)
        setStatus('\uD83D\uDCC4', 'Opening job ' + (idx + 1) + ' of ' + cards.length, '');
        if (titleLink) {
            titleLink.removeAttribute('target');
            titleLink.setAttribute('target', '_self');
        }
        humanClick(titleLink);

        // Step 5: Wait for detail panel (only relevant if split pane is active and same-tab navigation didn't unload page)
        const panel = await waitFor('.detail-view-container, .jd-pane, [class*="jobDetail"], [class*="detailView"]', 6000);
        if (!panel) {
            // We expect the page to have unloaded for same-tab navigation.
            // If it hasn't, sleep a bit more to be safe.
            await sleep(rand(2000, 4000));
            return;
        }
        await sleep(rand(1500, 3000));

        // Step 6: Simulate reading the JD (Fallback for split-pane view)
        var readMs  = rand(CFG.READ_MIN_MS, CFG.READ_MAX_MS);
        var readSec = Math.round(readMs / 1000);
        setStatus('\uD83D\uDCD6', 'Reading JD (split pane) \u2014 ~' + readSec + 's', 'job ' + (idx + 1));

        await sleep(rand(2500, 5000));
        await smoothScroll(panel, randInt(180, 480), rand(3000, 6000));
        await sleep(rand(3000, 7000));
        await smoothScroll(panel, randInt(0, 80), rand(1500, 3000));

        // Wait remaining read time
        var alreadySpent = 14000;
        await sleep(Math.max(0, readMs - alreadySpent));

        // Step 7: Done — record active time
        dailyStats.cycles++;
        dailyStats.activeMs += (Date.now() - cycleStart);
        saveDailyStats();
        updateTodayLine();
        setStatus('\u2705', 'JD read \u2014 job ' + (idx + 1), 'cycle ' + cycleCount + ' complete');
    }

    // =========================================================
    //  Main loop
    // =========================================================
    // =========================================================
    //  Job Details Reader (Option A: Same tab navigation)
    // =========================================================
    async function runJobDetailsReader() {
        setStatus('\uD83D\uDCD6', 'Reading Job Details...', 'Initialising...');
        await sleep(rand(3000, 5000));

        if (!isIdle()) {
            setStatus('\uD83D\uDC64', 'User active', 'Automation paused');
            return;
        }

        const readMs = rand(CFG.READ_MIN_MS, CFG.READ_MAX_MS);
        const readSec = Math.round(readMs / 1000);
        setStatus('\uD83D\uDCD6', 'Reading JD \u2014 ~' + readSec + 's', 'Scrolling down...');

        // Smooth scroll the main window
        const scrollTarget = randInt(400, 1100);
        await smoothScroll(document.documentElement, scrollTarget, rand(4000, 8000));
        await sleep(rand(3000, 6000));

        if (!isIdle()) {
            setStatus('\uD83D\uDC64', 'User active', 'Automation paused');
            return;
        }

        const scrollTarget2 = Math.min(scrollTarget + randInt(200, 600), document.documentElement.scrollHeight - 500);
        if (scrollTarget2 > scrollTarget) {
            await smoothScroll(document.documentElement, scrollTarget2, rand(3000, 6000));
            await sleep(rand(3000, 6000));
        }

        if (!isIdle()) {
            setStatus('\uD83D\uDC64', 'User active', 'Automation paused');
            return;
        }

        // Scroll back up a bit to simulate human review
        await smoothScroll(document.documentElement, Math.max(0, scrollTarget2 - randInt(300, 500)), rand(2500, 5000));

        // Sleep remaining read time
        const spent = Date.now() - pageLoadTime;
        if (readMs > spent) {
            await sleep(readMs - spent);
        }

        if (!isIdle()) {
            setStatus('\uD83D\uDC64', 'User active', 'Will not return automatically');
            return;
        }

        // Record stats
        dailyStats.cycles++;
        dailyStats.activeMs += (Date.now() - pageLoadTime);
        saveDailyStats();
        updateTodayLine();

        setStatus('\u2705', 'Finished reading', 'Returning to search...');
        await sleep(rand(1500, 3000));

        if (window.history.length > 1) {
            window.history.back();
        } else {
            location.href = CFG.SEARCH_PAGES[0];
        }
    }

    // =========================================================
    //  Main loop
    // =========================================================
    async function mainLoop() {
        await sleep(rand(3000, 6000));  // initial settle

        while (true) {
            let nextCycleTime = parseInt(localStorage.getItem('nke_next_cycle') || '0', 10);
            let now = Date.now();

            if (nextCycleTime > now && !paused && isIdle() && !isNight()) {
                let remaining = nextCycleTime - now;
                let mins = Math.ceil(remaining / 60000);
                setStatus('\u23F3', 'Next cycle in ~' + mins + 'm', nowStr());
                await sleep(Math.min(remaining, 10000));
                continue;
            }

            try {
                // Increment cycle count and persist
                cycleCount = parseInt(localStorage.getItem('nke_cycle_count') || '0', 10) + 1;
                localStorage.setItem('nke_cycle_count', String(cycleCount));

                await runCycle();
            } catch (err) {
                setStatus('\u26A0', 'Error in cycle', String(err).slice(0, 50));
                console.warn('[NKE] Cycle error:', err);
            }

            var delay;
            if (paused || !isIdle()) {
                delay = 25000;
            } else if (isNight()) {
                delay = 25 * 60 * 1000;
            } else {
                delay = rand(CFG.CYCLE_MIN_MS, CFG.CYCLE_MAX_MS);
            }

            localStorage.setItem('nke_next_cycle', String(Date.now() + delay));

            if (paused || !isIdle()) {
                await sleep(25000);
            } else if (isNight()) {
                await sleep(25 * 60 * 1000);
            } else {
                let targetTime = Date.now() + delay;
                while (Date.now() < targetTime && !paused && isIdle() && !isNight()) {
                    let mins = Math.ceil((targetTime - Date.now()) / 60000);
                    setStatus('\u23F3', 'Next cycle in ~' + mins + 'm', nowStr());
                    await sleep(Math.min(targetTime - Date.now(), 10000));
                }
            }
        }
    }

    // =========================================================
    //  Re-attach badge after SPA navigation
    // =========================================================
    new MutationObserver(function() {
        if (!document.getElementById('__nke_badge')) {
            statusEl = buildBadge();
        }
    }).observe(document.documentElement, { childList: true, subtree: false });

    // =========================================================
    //  Boot
    // =========================================================
    function boot() {
        if (/nlogin|checkout|payment/i.test(location.pathname)) {
            setTimeout(function() { location.href = CFG.SEARCH_PAGES[0]; }, 2000);
            return;
        }
        statusEl = buildBadge();
        
        if (isJobDetailsPage()) {
            setStatus('\uD83D\uDCD6', 'Job Details Page', 'Preparing reader...');
            console.log('[NKE] Naukri Engagement Assistant v1.0.0 — Reading Job Details');
            runJobDetailsReader();
        } else {
            setStatus('\u25B6', 'Engagement assistant active', 'Starting loop...');
            console.log('[NKE] Naukri Engagement Assistant v1.0.0 started');
            mainLoop();
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }

})();
