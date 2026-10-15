async function signIn() {
    const app = window.Telegram?.WebApp;
    const status = document.getElementById("miniapp-status");
    if (!app?.initData) {
        status.textContent = "Открой приложение кнопкой в нашем Telegram-боте.";
        return;
    }
    app.ready();
    app.expand();
    try {
        const response = await fetch("/api/auth/telegram", {
            method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ init_data: app.initData }),
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "Не удалось войти через Telegram.");
        window.location.replace("/");
    } catch (error) {
        status.textContent = error.message;
    }
}

signIn();
