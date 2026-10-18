# TailAdmin source attribution

The theme tokens and menu utilities in `theme.css` come from the MIT-licensed
[TailAdmin HTML template](https://github.com/TailAdmin/tailadmin-free-tailwind-dashboard-template),
version 2.4.0, commit `1bd2dc42a8467ae0281cf00bd0050da4c9c4be07`,
`src/css/style.css`. The original license is preserved in `LICENSE`.

`web/src/app.css` adapts the TailAdmin shell, cards, controls, badges, forms,
modals, sidebar and dark theme to the existing Python application's workflows.
Only the theme and menu utilities needed by this application are vendored;
Alpine, demo pages, chart libraries and external Google Fonts requests are omitted.

The locally served Outfit variable font is from `@fontsource-variable/outfit`
5.2.8 and is covered by `../OUTFIT-LICENSE` (SIL Open Font License).
