// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// /certificate (next-work plan step 15, certificates-plan stage 3): the box's CA to download, its
// fingerprint and what installing it allows, the steps for each platform, and a check of whether
// this device already trusts the box. A platform's steps are shown only once they have been tried
// on a real device (checked: true); until then, the general step. Never a "click through the
// warning" path: it teaches people to ignore certificate warnings.
const PLATFORMS = [
  { id: 'ios', title: 'iPhone and iPad', checked: false, steps: [
    'Download the certificate in Safari, and allow the profile to be downloaded.',
    'Settings → Profile Downloaded → Install.',
    'Then turn on full trust: Settings → General → About → Certificate Trust Settings, and switch on the Irate-Box CA.'] },
  { id: 'android', title: 'Android', checked: false, steps: [
    'Download the certificate.',
    'Settings → Security → Encryption and credentials → Install a certificate → CA certificate, and pick the file.',
    'Chrome trusts it; most apps don\'t. Firefox needs its own setting (Settings → About Firefox, tap the logo five times, then Use third-party CA certificates).'] },
  { id: 'windows', title: 'Windows', checked: false, steps: [
    'Download the certificate and open it.',
    'Install Certificate → Current User → Place all certificates in the following store → Trusted Root Certification Authorities.',
    'Chrome and Edge use it straight away; Firefox can be told to (security.enterprise_roots.enabled).'] },
  { id: 'macos', title: 'macOS', checked: false, steps: [
    'Download the certificate and open it: Keychain Access adds it to the login keychain.',
    'Double-click it there, open Trust, and set "When using this certificate" to Always Trust.'] },
  { id: 'linux', title: 'Linux', checked: false, steps: [
    'Chrome and Chromium: Settings → Privacy and security → Security → Manage certificates → Authorities → Import.',
    'Firefox: Settings → Privacy & Security → Certificates → View Certificates → Authorities → Import.',
    'For command-line tools: copy it to /usr/local/share/ca-certificates/ and run sudo update-ca-certificates.'] },
];

const $ = (id) => document.getElementById(id);
const make = (tag, text) => { const e = document.createElement(tag); if (text != null) e.textContent = text; return e; };

function httpsUrl(port) {
  return `https://${location.hostname}${port && port !== 443 ? `:${port}` : ''}/`;
}

async function trusted(port) {
  // An opaque request: it succeeds only if this device accepts the box's certificate.
  try {
    await fetch(`${httpsUrl(port)}certificate.json`, { mode: 'no-cors', cache: 'no-store' });
    return true;
  } catch (_) {
    return false;
  }
}

async function start() {
  let d;
  try {
    const r = await fetch('/certificate.json', { cache: 'no-store' });
    d = await r.json();
  } catch (_) {
    $('cert-state').textContent = 'The box did not answer.';
    return;
  }
  if (!d.set_up) {
    $('cert-state').textContent = 'This box has no certificate yet: its pages are served over plain HTTP. Its owner can make one on /admin → Security.';
    return;
  }
  const port = d.port || 443;
  $('cert-state').textContent = d.on
    ? `This box serves its pages over HTTPS too (${httpsUrl(port)}), with a certificate valid until ${new Date(d.expires * 1000).toLocaleDateString()}.`
    : 'This box has a certificate, but its owner has switched HTTPS off for now.';
  $('cert-names').textContent = d.names.join(', ');
  $('cert-fingerprint').textContent = d.fingerprint;
  $('cert-https').href = httpsUrl(port);
  $('cert-about').hidden = false;
  const steps = $('cert-steps');
  const shown = PLATFORMS.filter((p) => p.checked);
  if (shown.length) {
    shown.forEach((p) => {
      const fold = make('details');
      fold.append(make('summary', p.title));
      const ol = make('ol');
      p.steps.forEach((s) => ol.append(make('li', s)));
      fold.append(ol);
      steps.append(fold);
    });
  } else {
    steps.append(make('p', 'Download it, open it, and install it as a trusted certificate authority in your device\'s settings. '
      + 'Step-by-step guides for each kind of phone and computer follow once they have been tried on real devices.'));
  }
  if (location.protocol === 'https:') {
    $('cert-check').textContent = '✓ You are on HTTPS now: this device trusts the box.';
  } else if (d.on) {
    $('cert-check').textContent = 'Checking whether this device trusts the box…';
    $('cert-check').textContent = (await trusted(port))
      ? `✓ This device trusts the box: you can use ${httpsUrl(port)}.`
      : 'This device does not trust the box yet: install the certificate below.';
  }
}

start();
