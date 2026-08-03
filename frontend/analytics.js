(function () {
  const measurementId = window.PICKORA_CONFIG?.ga4MeasurementId?.trim();
  function recordLocal(name) {
    try {
      const metrics = JSON.parse(localStorage.getItem("pickoraAnalytics") || "{}");
      metrics[name] = (metrics[name] || 0) + 1;
      localStorage.setItem("pickoraAnalytics", JSON.stringify(metrics));
    } catch {}
  }

  if (measurementId && /^G-[A-Z0-9]+$/i.test(measurementId)) {
    window.dataLayer = window.dataLayer || [];
    window.gtag = function () { window.dataLayer.push(arguments); };
    window.gtag("js", new Date());
    window.gtag("config", measurementId);
    const script = document.createElement("script");
    script.async = true;
    script.src = `https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(measurementId)}`;
    document.head.appendChild(script);
  }

  document.addEventListener("click", function (event) {
    const link = event.target.closest("[data-affiliate-link]");
    if (!link) return;
    recordLocal("affiliate_click");
    if (typeof window.gtag === "function") {
      window.gtag("event", "affiliate_click", {
        link_url: link.href,
        link_domain: new URL(link.href).hostname,
        destination_domain: new URL(link.href).hostname,
        item_id: link.dataset.productId || "",
        item_name: link.dataset.productName || "",
        shop_id: link.dataset.shopId || "",
        placement: link.dataset.placement || "",
        outbound: true,
        transport_type: "beacon"
      });
      window.gtag("event", "begin_checkout", {
        currency: "THB",
        items: [{
          item_id: link.dataset.productId || "",
          item_name: link.dataset.productName || "",
          affiliation: "Shopee Affiliate"
        }]
      });
    }
  });

  document.addEventListener("pickora:search", function (event) {
    recordLocal("search");
    if (typeof window.gtag !== "function") return;
    window.gtag("event", "search", {
      search_term: event.detail.term,
      search_results: event.detail.resultCount,
      search_category: event.detail.category,
      search_sort: event.detail.sort
    });
  });

  document.addEventListener("pickora:engagement", function (event) {
    recordLocal(event.detail.name);
    if (event.detail.variant) {
      recordLocal(`${event.detail.name}_${event.detail.variant}`);
    }
    if (typeof window.gtag === "function") {
      const {name, ...parameters} = event.detail;
      window.gtag("event", name, parameters);
    }
  });

  const contextElement = document.getElementById("product-context");
  if (contextElement) {
    try {
      const product = JSON.parse(contextElement.textContent);
      if (typeof window.gtag === "function") {
        window.gtag("event", "view_item", {
          currency: "THB", value: Number(product.price) || 0,
          items: [{
            item_id: product.id, item_name: product.title,
            item_category: product.category, price: Number(product.price) || 0
          }]
        });
      }
      const recent = JSON.parse(localStorage.getItem("pickoraRecentlyViewed") || "[]")
        .filter(item => item?.id !== product.id);
      recent.unshift(product);
      localStorage.setItem("pickoraRecentlyViewed", JSON.stringify(recent.slice(0, 8)));
    } catch {
      // Storage can be unavailable in private browsing; product pages still work.
    }
  }

  document.addEventListener("click", function (event) {
    const link = event.target.closest('a[href^="/products/"]');
    if (!link || contextElement) return;
    if (typeof window.gtag === "function") {
      window.gtag("event", "select_item", {
        item_list_name: "Product discovery",
        items: [{item_id: link.href.split("/").filter(Boolean).pop() || ""}]
      });
    }
  });
}());
