// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// /certificate: the box's CA to download, its
// fingerprint and what installing it does, the steps for each platform, and a check of whether
// this device already trusts the box. A platform's steps are shown only once they have been tried
// on a real device (checked: true); until then, the general step. Never a "click through the
// warning" path: it teaches people to ignore certificate warnings.
const PLATFORMS = [
  // Tried on real phones: every screen in the order a phone shows it.
  { id: 'ios', title: 'iPhone and iPad', checked: true, steps: [
    'Open this page in Safari. Other browsers cannot give the file to Settings.',
    'Tap Download certificate.',
    'Tap Allow when Safari asks: "This website is trying to download a configuration profile".',
    'Open Settings. Tap "Profile Downloaded", near the top.',
    'Tap Install, in the top right corner.',
    'Enter your passcode.',
    'Tap Install again in the warning.',
    'Open Settings → General → About → Certificate Trust Settings. This is at the very bottom.',
    'Switch on the Irate-Box CA. Without this step, Safari still shows a warning. Tap Continue in the warning.',
    'Compare the SHA-256 fingerprint in the profile with the fingerprint on this page.',
    'To remove the certificate, open Settings → General → VPN & Device Management. Select the profile. Tap Remove Profile.'] },
  { id: 'android', title: 'Android', checked: true, steps: [
    'Tap Download certificate. If the browser asks whether to keep the file, keep it.',
    'Do not open the downloaded file. Since Android 11, a CA certificate can be installed only from Settings. The phone then shows this message: "Can\'t install CA certificates… must be installed in Settings". This is expected.',
    'Open Settings. Search for "CA certificate". Each phone maker puts this setting in a different place.',
    'If search does not find it, select Security and privacy → More security settings → Encryption and credentials → Install a certificate → CA certificate.',
    'On a Samsung phone, select Security and privacy → More security settings → Install from device storage → CA certificate.',
    'The phone shows a warning about an untrusted source. Tap Install anyway.',
    'The certificate covers only the names and addresses of this box (above). It does not cover other sites. Not every app enforces this limit.',
    'Unlock the phone with your PIN, pattern or fingerprint when asked. If the phone has no screen lock, Android asks you to set one.',
    'Select irate-box-ca.crt in Downloads. If the file picker opens in another folder, open its ☰ menu and select Downloads.',
    'The phone shows "CA certificate installed".',
    'Open Encryption and credentials → Trusted credentials → User. Select the Irate-Box CA. Compare its SHA-256 fingerprint with the fingerprint on this page.',
    'While the certificate is installed, Android shows "Network may be monitored" in the notifications or in Quick Settings. Android shows this for every certificate that you add. It is expected.',
    'To remove the certificate, open Trusted credentials → User. Select the Irate-Box CA. Select Remove.',
    'On current Android versions, Chrome and the system browser trust the certificate. Most other apps do not.',
    'Firefox needs its own setting. Open Settings → About Firefox. Tap the logo five times. Open Settings → Secret settings. Enable Use third-party CA certificates.'] },
  { id: 'windows', title: 'Windows', checked: false, steps: [
    'Download the certificate.',
    'Open the downloaded file.',
    'Select Install Certificate → Current User.',
    'Select Place all certificates in the following store → Trusted Root Certification Authorities.',
    'Chrome and Edge use the certificate at once. For Firefox, set security.enterprise_roots.enabled to true.'] },
  { id: 'macos', title: 'macOS', checked: false, steps: [
    'Download the certificate.',
    'Open the downloaded file. Keychain Access adds it to the login keychain.',
    'Compare its SHA-256 fingerprint with the fingerprint on this page.',
    'Double-click the certificate in Keychain Access. Open Trust.',
    'Set "When using this certificate" to Always Trust.'] },
  { id: 'linux', title: 'Linux', checked: false, steps: [
    'Chrome and Chromium: select Settings → Privacy and security → Security → Manage certificates → Authorities → Import.',
    'Firefox: select Settings → Privacy & Security → Certificates → View Certificates → Authorities → Import.',
    'For command-line tools, copy the certificate to /usr/local/share/ca-certificates/. Then run sudo update-ca-certificates.',
    'This command does not change the certificate store of a browser.'] },
];

const $ = (id) => document.getElementById(id);
const make = (tag, text) => { const node = document.createElement(tag); if (text != null) node.textContent = text; return node; };

// The host as a URL writes it: an IPv6 address in brackets.
const urlHost = (h) => (h.includes(':') && !h.startsWith('[') ? `[${h}]` : h);
const bare = (h) => h.replace(/^\[|\]$/g, '').toLowerCase();

function httpsUrl(port, host = location.hostname) {
  return `https://${urlHost(host)}${port && port !== 443 ? `:${port}` : ''}/`;
}

async function trusted(port, host) {
  // An opaque request: it succeeds only if this device accepts the box's certificate for that address.
  try {
    await fetch(`${httpsUrl(port, host)}certificate.json`, { mode: 'no-cors', cache: 'no-store' });
    return true;
  } catch (_) {
    return false;
  }
}

const link = (href, text = href) => { const anchor = make('a', text); anchor.href = href; return anchor; };

// The answer of the self-check, with the full HTTPS address to open when it is trusted.
function said(check, open, text, href) {
  check.textContent = text;
  open.replaceChildren();
  open.hidden = !href;
  if (href) open.append(make('span', 'Open '), link(href), make('span', '.'));
}

async function start() {
  let data;
  try {
    const response = await fetch('/certificate.json', { cache: 'no-store' });
    data = await response.json();
  } catch (_) {
    $('cert-state').textContent = 'The box did not answer.';
    return;
  }
  if (!data.set_up) {
    $('cert-state').textContent = 'This box has no certificate yet. It serves its pages over HTTP. An administrator can create one at /admin → Security → HTTPS.';
    return;
  }
  const port = data.port || 443;
  $('cert-state').textContent = data.on
    ? `This box also serves its pages over HTTPS (${httpsUrl(port)}). The certificate is valid until ${new Date(data.expires * 1000).toLocaleDateString()}.`
    : 'This box has a certificate. An administrator has disabled HTTPS.';
  $('cert-names').textContent = data.names.join(', ');
  $('cert-fingerprint').textContent = data.fingerprint;
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
    steps.append(make('p', `${shown.length ? `On other devices (${rest.join(', ')}): d` : 'D'}ownload the certificate. Then install it as a trusted certificate authority in the device settings. `
      + 'Compare its SHA-256 fingerprint with the fingerprint on this page before you trust it. '
      + 'Step-by-step instructions for these devices will follow after tests on real devices.'));
  }
  // The addresses the certificate covers, this page's own first. Opened at any other, the browser warns
  // whatever is installed: say so, and offer the addresses it does cover.
  const here = bare(location.hostname);
  const covered = [...(data.names || []), ...(data.addresses || [])].filter((h) => h && !h.startsWith('.'));
  const coversHere = covered.some((h) => bare(h) === here);
  if (!coversHere && covered.length) $('cert-https').href = httpsUrl(port, covered[0]);
  const list = $('cert-links');
  covered.forEach((h) => {
    const li = make('li');
    li.append(link(httpsUrl(port, h) + location.pathname.slice(1)));
    list.append(li);
  });
  $('cert-elsewhere').hidden = !data.on || !covered.length || (coversHere && covered.length < 2);
  const check = $('cert-check');
  const open = $('cert-open');
  const again = $('cert-again');
  if (!data.on) return;
  if (!coversHere) {
    said(check, open, `The certificate does not cover ${location.hostname}, the address this page was opened at. `
      + 'Over HTTPS at that address, every browser warns, even with the certificate installed. Use one of the addresses below.');
    return;
  }
  const hub = httpsUrl(port);
  if (location.protocol === 'https:') {
    said(check, open, 'You are on HTTPS. If your browser showed no warning, this device trusts the box and the certificate is installed.', hub);
    return;
  }
  const run = async () => {
    again.hidden = true;
    said(check, open, 'Checking whether this device trusts the box…');
    if (await trusted(port, here)) {
      said(check, open, '✓ The certificate is installed: this device trusts the box.', hub);
    } else {
      said(check, open, '✗ Not installed yet: this device does not trust the box, or the HTTPS connection failed. '
        + 'Install the certificate below, then select Check again.');
      again.hidden = false;
    }
  };
  $('cert-recheck').onclick = run;
  // Back from the device's settings: the page is shown again, so check again.
  document.addEventListener('visibilitychange', () => { if (!document.hidden && !again.hidden) run(); });
  await run();
}

start();
