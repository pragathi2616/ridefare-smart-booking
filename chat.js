(() => {
  const root = document.createElement("div");
  root.id = "ridefare-ai-support";
  root.innerHTML = `
    <button class="ai-chat-toggle" type="button" aria-label="Open AI Support Chat" aria-expanded="false">
      <span class="ai-chat-icon">✦</span>
      <span>AI Support</span>
    </button>

    <section class="ai-chat-panel" aria-label="RideFare AI Support" hidden>
      <div class="ai-chat-header">
        <div>
          <strong>RideFare AI Support</strong>
          <small>Instant help for your ride</small>
        </div>
        <button class="ai-chat-close" type="button" aria-label="Close chat">×</button>
      </div>

      <div class="ai-chat-messages">
        <div class="ai-message bot">
          Hi! 👋 I'm RideFare Support. Ask me about booking, fares, maps, payments, profile, password reset, or Render deployment.
        </div>
      </div>

      <div class="ai-chat-suggestions">
        <button type="button">How do I book?</button>
        <button type="button">How is fare calculated?</button>
        <button type="button">Forgot password</button>
        <button type="button">How does the map work?</button>
        <button type="button">How do I deploy on Render?</button>
      </div>

      <form class="ai-chat-form">
        <input type="text" placeholder="Type your question..." autocomplete="off" maxlength="500" required />
        <button type="submit" aria-label="Send">➤</button>
      </form>
    </section>
  `;
  document.body.appendChild(root);

  const toggle = root.querySelector(".ai-chat-toggle");
  const panel = root.querySelector(".ai-chat-panel");
  const close = root.querySelector(".ai-chat-close");
  const messages = root.querySelector(".ai-chat-messages");
  const form = root.querySelector(".ai-chat-form");
  const input = form.querySelector("input");

  function openChat() {
    panel.hidden = false;
    toggle.setAttribute("aria-expanded", "true");
    setTimeout(() => input.focus(), 50);
  }

  function closeChat() {
    panel.hidden = true;
    toggle.setAttribute("aria-expanded", "false");
  }

  function addMessage(text, type) {
    const el = document.createElement("div");
    el.className = `ai-message ${type}`;
    el.textContent = text;
    messages.appendChild(el);
    messages.scrollTop = messages.scrollHeight;
    return el;
  }

  async function ask(message) {
    addMessage(message, "user");
    const typing = addMessage("Thinking…", "bot typing");

    try {
      const response = await fetch("/api/support-chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message })
      });

      const data = await response.json();
      typing.remove();

      addMessage(
        data.reply || "Sorry, I couldn't process that request.",
        "bot"
      );
    } catch (error) {
      typing.remove();
      addMessage(
        "Support is temporarily unavailable. Please try again.",
        "bot"
      );
    }
  }

  toggle.addEventListener("click", () => {
    if (panel.hidden) openChat();
    else closeChat();
  });

  close.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    closeChat();
  });

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const value = input.value.trim();
    if (!value) return;
    input.value = "";
    ask(value);
  });

  root.querySelectorAll(".ai-chat-suggestions button").forEach((button) => {
    button.addEventListener("click", () => ask(button.textContent.trim()));
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !panel.hidden) closeChat();
  });
})();
