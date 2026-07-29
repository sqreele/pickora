(function () {
  const authPanel = document.getElementById("authPanel");
  const dashboard = document.getElementById("dashboard");
  const authMessage = document.getElementById("authMessage");
  const loadingState = document.getElementById("loadingState");
  const errorState = document.getElementById("errorState");
  const emptyState = document.getElementById("emptyState");
  const reportContent = document.getElementById("reportContent");
  let selectedDays = 28;

  function show(element, visible) {
    element.hidden = !visible;
  }

  function number(value) {
    return Number(value || 0).toLocaleString("th-TH");
  }

  function percent(value) {
    return `${(Number(value || 0) * 100).toLocaleString("th-TH", {maximumFractionDigits: 1})}%`;
  }

  function escapeHtml(value) {
    const element = document.createElement("span");
    element.textContent = String(value ?? "");
    return element.innerHTML;
  }

  function showLogin(message) {
    show(dashboard, false);
    show(authPanel, true);
    if (message) {
      authMessage.textContent = message;
      show(authMessage, true);
    }
  }

  function setReportState(state, detail = "") {
    show(loadingState, state === "loading");
    show(errorState, state === "error");
    show(emptyState, state === "empty");
    show(reportContent, state === "ready");
    if (state === "error") {
      document.getElementById("errorDetail").textContent = detail;
    }
  }

  async function checkSession() {
    const auth = new URLSearchParams(location.search).get("auth");
    if (auth === "failed") {
      showLogin("Access token ไม่ถูกต้อง");
      return;
    }
    if (auth === "unconfigured") {
      showLogin("เซิร์ฟเวอร์ยังไม่ได้ตั้งค่า access token สำหรับ Dashboard");
      return;
    }
    try {
      const response = await fetch("/api/analytics/session", {credentials: "same-origin"});
      if (response.status === 401) {
        showLogin();
        return;
      }
      if (!response.ok) throw new Error("session");
      show(authPanel, false);
      show(dashboard, true);
      await loadReport();
    } catch {
      showLogin("ไม่สามารถเชื่อมต่อ Analytics API ได้");
    }
  }

  async function loadReport() {
    setReportState("loading");
    document.getElementById("rangeDescription").textContent =
      `ข้อมูลถึงเมื่อวาน · ${selectedDays} วันที่ผ่านมา`;
    try {
      const response = await fetch(`/api/analytics/report?days=${selectedDays}`, {
        credentials: "same-origin", headers: {"Accept": "application/json"}
      });
      if (response.status === 401) {
        showLogin("Session หมดอายุ กรุณาเข้าสู่ระบบอีกครั้ง");
        return;
      }
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const messages = {
          analytics_not_configured: "ยังไม่ได้ตั้งค่า GA4 property หรือ service account บนเซิร์ฟเวอร์",
          ga4_api_error: "GA4 Data API ตอบกลับผิดพลาด กรุณาตรวจสิทธิ์ Property Viewer",
          invalid_date_range: "ช่วงวันที่ไม่ถูกต้อง"
        };
        throw new Error(messages[payload.error] || "เกิดข้อผิดพลาดจาก Analytics API");
      }
      const totals = payload.totals || {};
      const hasData = Number(totals.activeUsers || 0) + Number(totals.sessions || 0)
        + Number(totals.pageViews || 0) + Number(payload.affiliateClicks || 0) > 0;
      if (!hasData && !(payload.dailyTrend || []).length) {
        setReportState("empty");
        return;
      }
      renderReport(payload);
      setReportState("ready");
    } catch (error) {
      setReportState("error", error.message || "กรุณาลองใหม่ภายหลัง");
    }
  }

  function renderReport(report) {
    document.getElementById("activeUsers").textContent = number(report.totals.activeUsers);
    document.getElementById("sessions").textContent = number(report.totals.sessions);
    document.getElementById("pageViews").textContent = number(report.totals.pageViews);
    document.getElementById("engagementRate").textContent = percent(report.totals.engagementRate);
    document.getElementById("affiliateClicks").textContent = number(report.affiliateClicks);
    document.getElementById("landingRows").innerHTML = (report.landingPages || []).map(item => `
      <tr><td title="${escapeHtml(item.page)}">${escapeHtml(item.page || "(not set)")}</td>
      <td>${number(item.sessions)}</td><td>${number(item.activeUsers)}</td>
      <td>${number(item.pageViews)}</td><td>${percent(item.engagementRate)}</td></tr>`).join("")
      || '<tr><td colspan="5">ไม่มีข้อมูล Landing page</td></tr>';
    document.getElementById("trafficRows").innerHTML = (report.trafficSources || []).map(item => `
      <tr><td>${escapeHtml(item.source || "(direct)")} / ${escapeHtml(item.medium || "(none)")}</td>
      <td>${number(item.sessions)}</td><td>${number(item.activeUsers)}</td>
      <td>${percent(item.engagementRate)}</td></tr>`).join("")
      || '<tr><td colspan="4">ไม่มีข้อมูล Traffic source</td></tr>';
    const events = report.clickEvents || {};
    document.getElementById("eventGrid").innerHTML = Object.entries(events).map(([name, count]) =>
      `<article class="event-item"><span>${escapeHtml(name)}</span><strong>${number(count)}</strong></article>`
    ).join("") || '<p>ยังไม่มี outbound หรือ affiliate click events</p>';
    renderTrend(report.dailyTrend || []);
  }

  function renderTrend(rows) {
    const chart = document.getElementById("trendChart");
    if (!rows.length) {
      chart.innerHTML = '<div class="state-panel">ไม่มีข้อมูลรายวัน</div>';
      return;
    }
    const width = 1000;
    const height = 240;
    const padding = 24;
    const maximum = Math.max(1, ...rows.flatMap(row => [
      row.activeUsers, row.sessions, row.pageViews
    ].map(Number)));
    const x = index => padding + (index / Math.max(rows.length - 1, 1)) * (width - padding * 2);
    const y = value => height - padding - (Number(value) / maximum) * (height - padding * 2);
    const line = key => rows.map((row, index) =>
      `${index ? "L" : "M"}${x(index).toFixed(1)},${y(row[key]).toFixed(1)}`
    ).join(" ");
    const grid = [0, .25, .5, .75, 1].map(ratio => {
      const position = padding + ratio * (height - padding * 2);
      return `<line x1="${padding}" y1="${position}" x2="${width - padding}" y2="${position}" stroke="#eef0f3"/>`;
    }).join("");
    chart.innerHTML = `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-hidden="true">
      ${grid}
      <path d="${line("pageViews")}" fill="none" stroke="#667eea" stroke-width="3" vector-effect="non-scaling-stroke"/>
      <path d="${line("sessions")}" fill="none" stroke="#f6a500" stroke-width="3" vector-effect="non-scaling-stroke"/>
      <path d="${line("activeUsers")}" fill="none" stroke="#ee4d2d" stroke-width="3" vector-effect="non-scaling-stroke"/>
    </svg>`;
  }

  document.querySelectorAll("[data-days]").forEach(button => {
    button.addEventListener("click", () => {
      selectedDays = Number(button.dataset.days);
      document.querySelectorAll("[data-days]").forEach(item =>
        item.classList.toggle("active", item === button)
      );
      loadReport();
    });
  });
  document.getElementById("retryButton").addEventListener("click", loadReport);
  document.getElementById("logoutButton").addEventListener("click", async () => {
    await fetch("/api/analytics/logout", {
      method: "POST", credentials: "same-origin"
    }).catch(() => {});
    location.href = "/analytics/";
  });

  checkSession();
}());
