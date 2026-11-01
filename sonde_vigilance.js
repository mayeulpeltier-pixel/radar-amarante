// Sonde jetable : craque l'endpoint de donnees de plan-vigilance.org (SPA JS).
// Charge /recherche/, lance une recherche, capture les requetes de donnees
// (admin-ajax / wp-json / JSON externe) et leur forme. Lecture seule.
// A SUPPRIMER une fois l'endpoint connu et le collecteur cable.
const { chromium } = require('playwright');
const fs = require('fs');

(async () => {
  const sortie = 'sonde_vigilance_sortie';
  fs.mkdirSync(sortie, { recursive: true });
  const b = await chromium.launch();
  const p = await b.newPage();
  const reqs = [];
  const bodies = [];

  p.on('request', (req) => {
    const u = req.url();
    if (/\.(png|jpe?g|svg|woff2?|ttf|css|gif|ico|webp|mp4)(\?|$)/i.test(u)) return;
    if (/gtag|gstatic|google-analytics|doubleclick|fonts\.googleapis/i.test(u)) return;
    reqs.push(req.method() + ' ' + u + (req.postData() ? (' | POST ' + req.postData().slice(0, 300)) : ''));
  });
  p.on('response', async (r) => {
    try {
      const ct = r.headers()['content-type'] || '';
      const u = r.url();
      if (/json/i.test(ct) && !/wp-json\/?$/.test(u)) {
        const t = await r.text();
        if (t && t.length > 40 && /entrepr|plan|vigil|affaire|societe|mise|assignation|siren|\[/i.test(t)) {
          bodies.push({ url: u, status: r.status(), ct, extrait: t.slice(0, 600) });
        }
      }
    } catch (e) {}
  });

  const log = [];
  try {
    await p.goto('https://plan-vigilance.org/recherche/', { waitUntil: 'networkidle', timeout: 120000 });
  } catch (e) { log.push('goto: ' + String(e).slice(0, 100)); }
  await p.waitForTimeout(3000);

  // Lancer une recherche pour declencher l'appel de donnees.
  for (const q of ['totalenergies', 'edf']) {
    try {
      const inp = await p.$('input[name=q], input[type=search], input[type=text]');
      if (inp) { await inp.fill(q); await inp.press('Enter'); await p.waitForTimeout(4000); log.push('recherche "' + q + '" lancee'); }
      else { log.push('aucun champ q'); break; }
    } catch (e) { log.push('saisie ' + q + ': ' + String(e).slice(0, 80)); }
  }
  await p.waitForTimeout(2000);

  const uniq = [...new Set(reqs)];
  const rapport = [
    '# Sonde endpoint plan-vigilance', '',
    'Journal : ' + JSON.stringify(log), '',
    '## Requetes non-asset (' + uniq.length + ')',
    ...uniq.map((r) => '- ' + r.slice(0, 220)),
    '', '## Reponses JSON porteuses de donnees (' + bodies.length + ')',
    ...bodies.flatMap((x) => ['### ' + x.status + ' ' + x.url, '```', x.extrait, '```']),
  ].join('\n');

  fs.writeFileSync(sortie + '/endpoint.md', rapport);
  console.log(rapport.slice(0, 4000));
  if (process.env.GITHUB_STEP_SUMMARY) fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY, rapport);
  await b.close();
})();
