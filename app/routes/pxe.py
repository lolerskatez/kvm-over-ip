"""PXE boot management routes blueprint."""

import logging
from flask import Blueprint, render_template, jsonify, request, send_file, Response
from flask_login import login_required, current_user

from app.utils import require_admin, load_config, save_config

logger = logging.getLogger(__name__)

pxe_bp = Blueprint('pxe', __name__)

# Service instances
pxe_server = None


def init_pxe_services(pxe=None):
    """Initialize PXE services."""
    global pxe_server
    pxe_server = pxe


@pxe_bp.route('/pxe')
@login_required
def pxe_page():
    """PXE Boot management page."""
    return render_template('pxe.html')


@pxe_bp.route('/pxe/boot.ipxe')
def pxe_boot_menu():
    """Serve the iPXE boot menu script (unauthenticated — iPXE cannot send cookies)."""
    if not pxe_server:
        return 'No PXE server configured', 503
    menu = pxe_server.generate_boot_menu()
    return Response(menu, mimetype='text/plain')


@pxe_bp.route('/pxe/images/<path:name>/file')
def pxe_image_file(name):
    """Serve a boot image file over HTTP (unauthenticated — iPXE cannot send cookies)."""
    if not pxe_server:
        return jsonify({'error': 'PXE not configured'}), 503
    image_path = pxe_server.get_image_path(name)
    if not image_path:
        return jsonify({'error': 'Image not found'}), 404
    return send_file(str(image_path), mimetype='application/octet-stream')


@pxe_bp.route('/api/pxe/status', methods=['GET'])
@login_required
def api_pxe_status():
    """Get PXE server status."""
    if not pxe_server:
        return jsonify({'error': 'PXE not configured'}), 503
    return jsonify(pxe_server.get_status())


@pxe_bp.route('/api/pxe/start', methods=['POST'])
@login_required
@require_admin
def api_pxe_start():
    """Start the PXE server (admin only)."""
    if not pxe_server:
        return jsonify({'error': 'PXE not configured'}), 503
    if pxe_server.start():
        logger.info(f"PXE server started by {current_user.username}")
        return jsonify({'status': 'ok'})
    return jsonify({'error': 'Failed to start PXE server'}), 500


@pxe_bp.route('/api/pxe/stop', methods=['POST'])
@login_required
@require_admin
def api_pxe_stop():
    """Stop the PXE server (admin only)."""
    if not pxe_server:
        return jsonify({'error': 'PXE not configured'}), 503
    pxe_server.stop()
    logger.info(f"PXE server stopped by {current_user.username}")
    return jsonify({'status': 'ok'})


@pxe_bp.route('/api/pxe/config', methods=['GET', 'POST'])
@login_required
@require_admin
def api_pxe_config():
    """Get or update PXE server configuration (admin only)."""
    if not pxe_server:
        return jsonify({'error': 'PXE not configured'}), 503

    if request.method == 'GET':
        return jsonify(pxe_server.to_config_dict())

    data = request.get_json()
    allowed = ('interface', 'dhcp_range', 'server_ip', 'base_dir',
                'netbootxyz_enabled', 'netbootxyz_url', 'netbootxyz_efi_url')
    pxe_cfg = {}
    for key in allowed:
        if key in data:
            pxe_cfg[key] = data[key]
    # string-coerce the path/IP fields
    for key in ('interface', 'dhcp_range', 'server_ip', 'base_dir',
                'netbootxyz_url', 'netbootxyz_efi_url'):
        if key in pxe_cfg:
            pxe_cfg[key] = str(pxe_cfg[key]).strip()

    pxe_server.apply_config(pxe_cfg)

    # Persist into main config.json
    config = load_config()
    config['pxe'] = pxe_server.to_config_dict()
    save_config(config)

    # Regenerate dnsmasq config (takes effect on next start/restart)
    pxe_server.write_config()

    logger.info(f"PXE config updated by {current_user.username}")
    return jsonify({'status': 'ok', 'config': pxe_server.to_config_dict()})


@pxe_bp.route('/api/pxe/images', methods=['GET'])
@login_required
def api_pxe_images():
    """List available PXE boot images."""
    if not pxe_server:
        return jsonify({'error': 'PXE not configured'}), 503
    return jsonify({'images': pxe_server.list_images()})


@pxe_bp.route('/api/pxe/images/upload', methods=['POST'])
@login_required
@require_admin
def api_pxe_upload():
    """Upload a boot image (admin only)."""
    if not pxe_server:
        return jsonify({'error': 'PXE not configured'}), 503

    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    f = request.files['file']
    if not f.filename:
        return jsonify({'error': 'No filename'}), 400

    from app.services.pxe.pxe_server import PXEServer
    filename = PXEServer.sanitize_filename(f.filename)
    result = pxe_server.save_image(filename, f.stream)
    if result:
        logger.info(f"PXE image uploaded by {current_user.username}: {filename}")
        return jsonify({'status': 'ok', 'image': result})
    return jsonify({'error': 'Failed to save image'}), 500


@pxe_bp.route('/api/pxe/images/<name>', methods=['DELETE'])
@login_required
@require_admin
def api_pxe_delete_image(name):
    """Delete a boot image (admin only)."""
    if not pxe_server:
        return jsonify({'error': 'PXE not configured'}), 503

    from app.services.pxe.pxe_server import PXEServer
    safe_name = PXEServer.sanitize_filename(name)
    if pxe_server.delete_image(safe_name):
        logger.info(f"PXE image deleted by {current_user.username}: {safe_name}")
        return jsonify({'status': 'ok'})
    return jsonify({'error': 'Image not found or could not be deleted'}), 404


@pxe_bp.route('/api/pxe/dependencies', methods=['GET'])
@login_required
def api_pxe_dependencies():
    """Check PXE system dependencies (dnsmasq, iPXE binaries)."""
    if not pxe_server:
        return jsonify({'error': 'PXE not configured'}), 503
    return jsonify(pxe_server.check_dependencies())


@pxe_bp.route('/api/pxe/catalog', methods=['GET'])
@login_required
def api_pxe_catalog():
    """Return the full OS catalog with an 'enabled' field per entry."""
    from app.services.pxe.boot_catalog import BOOT_CATALOG
    enabled = set(pxe_server.enabled_catalog_ids) if pxe_server else set()
    catalog = [{**entry, 'enabled': entry['id'] in enabled} for entry in BOOT_CATALOG]
    return jsonify({'catalog': catalog})


@pxe_bp.route('/api/pxe/catalog/enabled', methods=['POST'])
@login_required
@require_admin
def api_pxe_catalog_enabled():
    """Set which catalog entry IDs are active in the boot menu (admin only)."""
    if not pxe_server:
        return jsonify({'error': 'PXE not configured'}), 503
    data = request.get_json() or {}
    ids = [str(i) for i in data.get('enabled_ids', [])]
    pxe_server.enabled_catalog_ids = ids
    cfg = load_config()
    cfg['pxe'] = pxe_server.to_config_dict()
    save_config(cfg)
    pxe_server.write_config()
    return jsonify({'status': 'ok', 'enabled_ids': ids})


@pxe_bp.route('/api/pxe/menu-preview', methods=['GET'])
@login_required
def api_pxe_menu_preview():
    """Return the current iPXE boot script for the UI boot-menu preview."""
    if not pxe_server:
        return jsonify({'error': 'PXE not configured'}), 503
    return jsonify({'script': pxe_server.generate_boot_menu()})
