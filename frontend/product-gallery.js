document.querySelectorAll(".product-gallery").forEach(gallery => {
  const mainImage = gallery.querySelector("[data-gallery-main]");
  const thumbnails = [...gallery.querySelectorAll("[data-gallery-image]")];

  thumbnails.forEach(thumbnail => {
    thumbnail.addEventListener("click", () => {
      mainImage.src = thumbnail.dataset.galleryImage;
      thumbnails.forEach(item => {
        const selected = item === thumbnail;
        item.classList.toggle("active", selected);
        item.setAttribute("aria-pressed", String(selected));
      });
    });
  });
});
