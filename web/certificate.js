// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// /certificate (next-work plan step 15, certificates-plan stage 3): the box's CA to download, its
// fingerprint and what installing it allows, the steps for each platform, and a check of whether
// this device already trusts the box. A platform's steps are shown only once they have been tried
// on a real device (checked: true); until then, the general step. Never a "click through the
// warning" path: it teaches people to ignore certificate warnings.
const PLATFORMS = [
  // Tried on real phones (Tom, 2026-10-08: "painful on android because its an untrusted source, but
  // it's doable. It needs a help tutorial"): every screen in the order a phone shows it.
  { id: 'ios', title: 'iPhone and iPad', checked: true, steps: [
    'Open this page in Safari (not another browser: only Safari hands the file to Settings), and tap Download the certificate. Allow the download when asked: "This website is trying to download a configuration profile".',
    'Open Settings: "Profile Downloaded" is near the top. Tap it, then Install (top right), enter your passcode, and Install again through the warning.',
    'Turn on full trust, or Safari still warns: Settings → General → About → Certificate Trust Settings (at the very bottom), and switch on the Irate-Box CA. Continue through the warning.',
    'To remove it later: Settings → General → VPN & Device Management → the profile → Remove Profile.'] },
  { id: 'android', title: 'Android', checked: true, steps: [
    'Tap Download the certificate. If the browser asks whether to keep the file, keep it. Tapping the downloaded file does nothing useful: since Android 11 a CA certificate can only be installed from Settings, and the phone says so ("Can\'t install CA certificates… must be installed in Settings"). That is expected.',
    'Open Settings and search for "CA certificate" (the quickest way: every maker hides it somewhere else). Without search: Settings → Security and privacy → More security settings → Encryption and credentials → Install a certificate → CA certificate. On Samsung: Settings → Security and privacy → More security settings → Install from device storage → CA certificate.',
    'The phone warns that your data won\'t be private: this is the "untrusted source" step. Tap Install anyway. The certificate can vouch only for this box\'s names and addresses (above), not for other sites.',
    'Unlock with your PIN, pattern or fingerprint when asked. Android insists on a screen lock for this; if you have none, it asks you to set one.',
    'Pick irate-box-ca.crt from Downloads (in the file picker\'s ☰ menu if it opens somewhere else). "CA certificate installed" confirms it.',
    'Check it: Encryption and credentials → Trusted credentials → the User tab shows the Irate-Box CA. Tap it to compare its SHA-256 fingerprint with the one above.',
    'Android then shows "Network may be monitored" (in the notifications or Quick Settings) for as long as it is installed: that is the phone being honest about a certificate you added, not something wrong. To remove it, tap it under Trusted credentials → User → Remove.',
    'Chrome and the system browser trust it; most other apps don\'t, and Firefox needs its own setting (Settings → About Firefox, tap the logo five times, then Settings → Secret settings → Use third-party CA certificates).'] },
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
  }
  // The rest: the general step until their guides have been tried on a real device.
  const rest = PLATFORMS.filter((p) => !p.checked).map((p) => p.title);
  if (rest.length) {
    steps.append(make('p', `${shown.length ? `On other devices (${rest.join(', ')}): d` : 'D'}ownload it, open it, and install it as a trusted certificate authority in the device's settings. `
      + 'Step-by-step guides for them follow once they have been tried on real devices.'));
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
