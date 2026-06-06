"""Minimal WebDAV bridge — lets Obsidian open the vault directly.

Implements just enough of the WebDAV protocol (RFC 4918) for Obsidian:
OPTIONS, PROPFIND, GET, PUT, MKCOL. No external dependencies — pure Flask
+ ElementTree.

Mount in Obsidian as: https://host/ai-provider/memory/dav
Auth via Bearer token, or Basic Auth (username=user_id, password=SERVICE_TOKEN).
OPTIONS responses are unauthenticated so WebDAV/CORS preflight succeeds.
"""

from __future__ import annotations
import os
from pathlib import Path
from xml.etree import ElementTree as ET
from flask import Blueprint, request, jsonify, Response, g
from config import Config
from api.auth import require_token_or_basic, _asserted_user_id

webdav_bp = Blueprint('webdav', __name__, url_prefix='/memory/dav')

_DAV_NS = 'DAV:'
_XML_HEADERS = {'Content-Type': 'application/xml; charset="utf-8"'}
_DAV_METHODS_ALLOWED = 'OPTIONS, PROPFIND, GET, PUT, MKCOL'


def _options_response() -> Response:
    """Static OPTIONS response with proper WebDAV capability advertisement.

    Required by WebDAV clients (Obsidian Remotely Save, macOS Finder,
    davfs2) to know which methods are supported. Without correct Allow
    and DAV headers many clients fail the initial capability handshake
    and never attempt PROPFIND.

    Returned without an auth check on purpose — capability discovery
    happens before clients have a chance to attach credentials. The
    response carries no user-scoped data so this is safe.
    """
    return Response('', status=200, headers={
        'Allow': _DAV_METHODS_ALLOWED,
        'DAV': '1, 2',
        'MS-Author-Via': 'DAV',
        'Content-Length': '0',
    })


def _gate():
    if not Config.MEMORY_ENABLED:
        return jsonify({'error': 'memory feature disabled'}), 503
    return None


def _scope_user_id() -> str:
    if g.principal.role == 'admin':
        return request.args.get('user') or _asserted_user_id() or g.principal.user_id
    return g.principal.user_id


def _user_root() -> Path:
    return Path(Config.VAULT_PATH) / _scope_user_id()


def _dav_path(rel: str) -> Path:
    """Resolve a WebDAV path to a filesystem path under the user's vault."""
    if not rel or rel == '/':
        return _user_root()
    rel = rel.strip('/')
    if '..' in rel.split('/') or rel.startswith('/'):
        raise ValueError('invalid path')
    candidate = (_user_root() / rel).resolve()
    candidate.relative_to(_user_root().resolve())
    return candidate


def _propfind_xml(path: Path, base_path: Path, base_href: str) -> str:
    """Generate a multistatus PROPFIND response."""
    multistatus = ET.Element(f'{{{_DAV_NS}}}multistatus')

    def add_response(item_path: Path, href: str):
        resp = ET.SubElement(multistatus, f'{{{_DAV_NS}}}response')
        href_el = ET.SubElement(resp, f'{{{_DAV_NS}}}href')
        href_el.text = href
        propstat = ET.SubElement(resp, f'{{{_DAV_NS}}}propstat')
        prop = ET.SubElement(propstat, f'{{{_DAV_NS}}}prop')
        # resource type
        restype = ET.SubElement(prop, f'{{{_DAV_NS}}}resourcetype')
        if item_path.is_dir():
            ET.SubElement(restype, f'{{{_DAV_NS}}}collection')
        # display name
        dn = ET.SubElement(prop, f'{{{_DAV_NS}}}displayname')
        dn.text = item_path.name or '/'
        # getlastmodified
        if item_path.exists():
            import datetime
            mtime = datetime.datetime.fromtimestamp(
                item_path.stat().st_mtime, tz=datetime.timezone.utc
            )
            glm = ET.SubElement(prop, f'{{{_DAV_NS}}}getlastmodified')
            glm.text = mtime.strftime('%a, %d %b %Y %H:%M:%S GMT')
        # getcontenttype
        gct = ET.SubElement(prop, f'{{{_DAV_NS}}}getcontenttype')
        if item_path.is_file():
            gct.text = 'text/markdown; charset=utf-8'
        else:
            gct.text = 'httpd/unix-directory'
        # getcontentlength
        gcl = ET.SubElement(prop, f'{{{_DAV_NS}}}getcontentlength')
        if item_path.is_file():
            gcl.text = str(item_path.stat().st_size)
        else:
            gcl.text = '0'
        ET.SubElement(propstat, f'{{{_DAV_NS}}}status').text = 'HTTP/1.1 200 OK'

    add_response(base_path, base_href)

    if path.is_dir():
        depth = request.headers.get('Depth', '1')
        if depth == '1' or depth == 'infinity':
            for child in sorted(path.iterdir()):
                child_href = f'{base_href.rstrip("/")}/{child.name}'
                if child.is_dir():
                    child_href += '/'
                add_response(child, child_href)

    return ET.tostring(multistatus, encoding='unicode', xml_declaration=True)


@webdav_bp.route('', methods=['OPTIONS'], strict_slashes=False)
@webdav_bp.route('/', methods=['OPTIONS'], strict_slashes=False)
@webdav_bp.route('/<path:subpath>', methods=['OPTIONS'], strict_slashes=False)
def handle_options(subpath: str = ''):
    return _options_response()


@webdav_bp.route('/<path:subpath>', methods=['PROPFIND', 'GET', 'PUT', 'MKCOL'], strict_slashes=False)
@require_token_or_basic
def handle(subpath: str):
    gate = _gate()
    if gate:
        return gate

    try:
        fs_path = _dav_path(subpath)
    except (ValueError, AttributeError):
        return ('', 404)

    method = request.method.upper()

    # PROPFIND — list directory
    if method == 'PROPFIND':
        if not fs_path.exists():
            return ('', 404)
        base_href = request.path
        xml = _propfind_xml(fs_path, _user_root().resolve(), base_href)
        return Response(xml, 207, _XML_HEADERS)

    # GET — read file
    if method == 'GET':
        if not fs_path.is_file():
            return ('', 404)
        return Response(fs_path.read_bytes(), mimetype='text/markdown')

    # PUT — write/overwrite file
    if method == 'PUT':
        body = request.get_data()
        fs_path.parent.mkdir(parents=True, exist_ok=True)
        fs_path.write_bytes(body)
        return ('', 204)

    # MKCOL — create directory
    if method == 'MKCOL':
        if fs_path.exists():
            return ('', 405)
        fs_path.mkdir(parents=True)
        return ('', 201)

    return ('', 405)


@webdav_bp.route('', methods=['PROPFIND'], strict_slashes=False)
@require_token_or_basic
def handle_root():
    gate = _gate()
    if gate:
        return gate
    fs_path = _user_root()
    if not fs_path.exists():
        fs_path.mkdir(parents=True, exist_ok=True)
    xml = _propfind_xml(fs_path, _user_root(), '/')
    return Response(xml, 207, _XML_HEADERS)
