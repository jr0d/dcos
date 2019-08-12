import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlparse

PACKAGE_NAME = 'dcos-bootstrap-ca'
BINARY_PATH = '/genconf/bin'
CA_PATH = '/genconf/ca'


def _check(config: Dict[str, Any]) -> List[str]:
    """
    Current constraints are:
        config.yaml:
            - exhibitor_tls_enabled == True,
            - bootstrap_url must not be a local path
                This is necessary to prevent this orchestration
                from running when gen is not executed on a proper
                bootstrap node.
            - master_discovery must be static
            - DC/OS variant must be enterprise
    """
    checks = [
        (lambda: config.get('exhibitor_tls_enabled', False) == 'true',
         'Exhibitor security is disabled'),
        (lambda: config['master_discovery'] == 'static',
         'Only static master discovery is supported'),
        (lambda: config['dcos_variant'] == 'enterprise',
         'Exhibitor security is an enterprise feature'),
        (lambda: urlparse(config['bootstrap_url']).scheme != 'file',
         'Blackbox exhibitor security is only supported when using a remote'
         ' bootstrap node'),
    ]

    reasons = []
    for check in checks:
        if not check[0]():
            reasons.append(check[1])

    return reasons


def _find_package(packages_json: str) -> str:
    packages = json.loads(packages_json)
    for package in packages:
        if package.startswith(PACKAGE_NAME):
            return package
    raise Exception('{} package is not present'.format(PACKAGE_NAME))


def _extract_package(package_path: str) -> None:
    """ Extracts the dcos-bootstrap-ca package from the local pkgpanda
    repository """
    Path(BINARY_PATH).mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        subprocess.run(['tar', '-xJf', package_path, '-C', td])

        shutil.move(
            Path(td) / 'bin' / PACKAGE_NAME,
            Path(BINARY_PATH) / PACKAGE_NAME)


def _init_ca(alt_names: List[str]) -> None:
    """ Initializes the CA (generates a private key and self signed
    certificate CA extensions) if the resulting files do not already exist """
    root_ca_paths = [
        Path(CA_PATH) / 'root-cert.pem', Path(CA_PATH) / 'root-key.pem']
    if not all(map(lambda p: p.exists(), root_ca_paths)):
        Path(CA_PATH).mkdir(mode=0o700, exist_ok=True)
        cmd_path = Path(BINARY_PATH) / PACKAGE_NAME
        subprocess.run([
            str(cmd_path), '-d', CA_PATH, 'init-ca', '--sans', ','.join(alt_names)
        ])
    else:
        print('[{}] CA files already exist'.format(__name__))


def _get_ca_alt_name(config: Dict[str, Any]) -> str:
    """ Gets the bootstrap url hostname. Used to populate the CA SAN
    extension """
    url = config['exhibitor_bootstrap_ca_url'] or config['bootstrap_url']
    return urlparse(url).hostname or ""


def initialize_exhibitor_ca(final_arguments: Dict[str, Any]) -> None:
    if final_arguments['platform'] != 'onprem':
        return

    reasons = _check(final_arguments)
    if reasons:
        print('[{}] not bootstrapping exhibitor CA: {}'.format(
            __name__, '\n'.join(reasons)))
        final_arguments['exhibitor_ca_certificate'] = ""
        final_arguments['exhibitor_ca_certificate_path'] = "/dev/null"
        return

    package_filename = _find_package(
        final_arguments['cluster_packages']) + '.tar.xz'
    package_path = Path('/artifacts/packages') / PACKAGE_NAME / package_filename

    ca_alternative_names = ['127.0.0.1', 'localhost',
                            'exhibitor', _get_ca_alt_name(final_arguments)]

    _extract_package(package_path)
    _init_ca(ca_alternative_names)

    # inject
    final_arguments['exhibitor_ca_certificate'] = Path(CA_PATH).read_text(
        encoding='ascii')
    final_arguments['exhibitor_ca_certificate_path'] = '/tmp/root-cert.pem'
