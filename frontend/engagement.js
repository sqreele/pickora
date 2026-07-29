(function () {
  function emit(name, detail) {
    document.dispatchEvent(new CustomEvent("pickora:engagement", {detail: {name, ...detail}}));
  }

  document.querySelectorAll("[data-native-share]").forEach(button => {
    button.addEventListener("click", async () => {
      const data = {title: button.dataset.shareTitle, url: button.dataset.shareUrl};
      try {
        if (navigator.share) await navigator.share(data);
        else await navigator.clipboard.writeText(data.url);
        emit("share", {method: navigator.share ? "native" : "copy"});
      } catch (error) {
        if (error.name !== "AbortError") console.error(error);
      }
    });
  });

  document.querySelectorAll(".share-actions a").forEach(link => {
    link.addEventListener("click", () => emit("share", {method: link.textContent.trim().toLowerCase()}));
  });

  document.querySelectorAll("[data-compare-product]").forEach(button => {
    button.addEventListener("click", () => {
      const selected = JSON.parse(localStorage.getItem("pickoraCompare") || "[]")
        .filter(id => id !== button.dataset.compareProduct);
      selected.unshift(button.dataset.compareProduct);
      localStorage.setItem("pickoraCompare", JSON.stringify(selected.slice(0, 4)));
      emit("add_to_compare", {product_id: button.dataset.compareProduct});
      window.location.href = "/compare-products/";
    });
  });

  const cta = document.querySelector("[data-affiliate-link]");
  if (cta) {
    let variant = localStorage.getItem("pickoraCtaVariant");
    if (!["A", "B"].includes(variant)) {
      variant = Math.random() < 0.5 ? "A" : "B";
      localStorage.setItem("pickoraCtaVariant", variant);
    }
    if (variant === "B") cta.textContent = "ดูราคาและโปรโมชันล่าสุด →";
    cta.dataset.ctaVariant = variant;
    emit("experiment_impression", {experiment_id: "product_cta_v1", variant});
    cta.addEventListener("click", () => emit("experiment_conversion", {
      experiment_id: "product_cta_v1", variant
    }));
  }

  const form = document.getElementById("emailSignup");
  if (form) {
    form.addEventListener("submit", async event => {
      event.preventDefault();
      const status = document.getElementById("emailSignupStatus");
      const endpoint = window.PICKORA_CONFIG?.emailSubscribeEndpoint?.trim();
      if (!endpoint) {
        status.textContent = "ยังไม่ได้ตั้งค่าระบบรับอีเมล";
        return;
      }
      status.textContent = "กำลังสมัคร...";
      try {
        const response = await fetch(endpoint, {
          method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({email: form.email.value.trim(), source: "pickora"})
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        form.reset();
        status.textContent = "สมัครเรียบร้อยแล้ว";
        emit("sign_up", {method: "email"});
      } catch {
        status.textContent = "สมัครไม่สำเร็จ กรุณาลองใหม่";
      }
    });
  }
}());
