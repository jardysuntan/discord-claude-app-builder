module.exports = {
  apps: [{
    name: "discord-claude-bridge",
    script: "bot.py",
    interpreter: "python3",
    cwd: __dirname,
    watch: ["*.py", "commands/*.py", "handlers/*.py", "views/*.py", "helpers/*.py"],
    watch_delay: 2000,
    ignore_watch: ["__pycache__", "*.pyc", ".env", "workspaces.json"],
    max_restarts: 10,
    restart_delay: 3000,
    env: { PYTHONUNBUFFERED: "1" },
  }],
};
