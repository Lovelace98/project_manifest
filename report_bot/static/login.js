"use strict";

document.querySelector("[data-password-toggle]")?.addEventListener("click", (event) => {
  const button = event.currentTarget;
  const input = document.getElementById(button.getAttribute("aria-controls"));
  const reveal = input.type === "password";
  input.type = reveal ? "text" : "password";
  button.textContent = reveal ? "Hide" : "Show";
});
