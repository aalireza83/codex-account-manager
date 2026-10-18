const {copyFileSync}=require('node:fs');
const {spawnSync}=require('node:child_process');
const path=require('node:path');
const root=path.resolve(__dirname,'..');
copyFileSync(path.join(root,'node_modules/@fontsource-variable/outfit/files/outfit-latin-wght-normal.woff2'),path.join(root,'web/assets/outfit-latin.woff2'));
const result=spawnSync(process.execPath,[path.join(root,'node_modules/@tailwindcss/cli/dist/index.mjs'),'-i','web/src/app.css','-o','web/assets/app.css','--minify'],{cwd:root,stdio:'inherit'});
process.exit(result.status??1);
