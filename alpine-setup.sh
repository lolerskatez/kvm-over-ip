#!/bin/bash
##############################################################################
# Alpine Linux + Docker Setup Script for KVM-over-IP
# 
# This script automates the setup of Alpine Linux on a thin client for
# running KVM-over-IP in Docker. Run this AFTER initial Alpine installation
# and before installing KVM-over-IP.
#
# Prerequisites:
#   - Fresh Alpine Linux installation on eMMC
#   - 64GB USB drive connected (for Docker storage)
#   - Network connectivity
#   - Root access
#
# Usage:
#   sudo bash alpine-setup.sh [OPTIONS]
#
# Options:
#   --hostname NAME         Set system hostname (default: igel-docker)
#   --admin-user USER       Create admin user (default: admin)
#   --timezone TZ           Set timezone (default: America/Chicago)
#   --usb-device /dev/sdX   Specify USB device (default: auto-detect)
#   --docker-size SIZE      Docker storage size in GB (default: 60)
#   --zram-size SIZE        zram swap size in MB (default: 1024)
#   --help                  Show this help message
#
##############################################################################

set -e  # Exit on error
set -o pipefail

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default configuration
HOSTNAME="igel-docker"
ADMIN_USER="admin"
TIMEZONE="America/Chicago"
USB_DEVICE=""
DOCKER_SIZE="60"
ZRAM_SIZE="1024"
SKIP_INTERACTIVE=false

# Logging functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Parse command line arguments
parse_arguments() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --hostname)
                HOSTNAME="$2"
                shift 2
                ;;
            --admin-user)
                ADMIN_USER="$2"
                shift 2
                ;;
            --timezone)
                TIMEZONE="$2"
                shift 2
                ;;
            --usb-device)
                USB_DEVICE="$2"
                shift 2
                ;;
            --docker-size)
                DOCKER_SIZE="$2"
                shift 2
                ;;
            --zram-size)
                ZRAM_SIZE="$2"
                shift 2
                ;;
            --skip-interactive)
                SKIP_INTERACTIVE=true
                shift
                ;;
            --help)
                show_help
                exit 0
                ;;
            *)
                log_error "Unknown option: $1"
                show_help
                exit 1
                ;;
        esac
    done
}

show_help() {
    head -n 30 "$0" | tail -n 20
}

# Check if running as root
check_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "This script must be run as root"
        exit 1
    fi
}

# Detect USB device if not specified
detect_usb_device() {
    if [[ -n "$USB_DEVICE" ]]; then
        return
    fi
    
    log_info "Detecting USB devices..."
    
    # Get all block devices excluding eMMC and loop devices
    local usb_devices=()
    for device in $(lsblk -dn -o NAME | grep -v mmcblk | grep -v loop); do
        usb_devices+=("/dev/$device")
    done
    
    if [[ ${#usb_devices[@]} -eq 0 ]]; then
        log_error "No USB devices found. Please specify with --usb-device"
        exit 1
    elif [[ ${#usb_devices[@]} -eq 1 ]]; then
        USB_DEVICE="${usb_devices[0]}"
        log_success "Found USB device: $USB_DEVICE"
    else
        log_warning "Multiple USB devices found:"
        for i in "${!usb_devices[@]}"; do
            echo "  $((i+1)). ${usb_devices[$i]}"
        done
        
        if [[ "$SKIP_INTERACTIVE" == true ]]; then
            log_error "Please specify USB device with --usb-device"
            exit 1
        fi
        
        read -p "Select device (number): " choice
        USB_DEVICE="${usb_devices[$((choice-1))]}"
    fi
    
    # Safety check
    if [[ ! -b "$USB_DEVICE" ]]; then
        log_error "Device $USB_DEVICE not found"
        exit 1
    fi
}

# Confirm configuration
confirm_configuration() {
    if [[ "$SKIP_INTERACTIVE" == true ]]; then
        return
    fi
    
    log_info "Configuration summary:"
    echo "  Hostname:     $HOSTNAME"
    echo "  Admin user:   $ADMIN_USER"
    echo "  Timezone:     $TIMEZONE"
    echo "  USB device:   $USB_DEVICE"
    echo "  Docker size:  ${DOCKER_SIZE}GB"
    echo "  zram swap:    ${ZRAM_SIZE}MB"
    echo ""
    echo "WARNING: This will format $USB_DEVICE"
    read -p "Continue? (yes/no): " -r
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        log_error "Setup cancelled"
        exit 1
    fi
}

# System updates
update_system() {
    log_info "Updating system packages..."
    apk update
    apk upgrade -y
}

# Enable community repository
enable_community_repo() {
    log_info "Enabling community repository..."
    sed -i 's|^#http|http|' /etc/apk/repositories
    apk update
}

# Install required packages
install_packages() {
    log_info "Installing required packages..."
    apk add -y \
        bash \
        curl \
        git \
        htop \
        iotop \
        ncdu \
        nano \
        usbutils \
        v4l-utils \
        doas \
        openssh \
        openssh-client \
        docker \
        docker-cli \
        docker-compose \
        parted \
        zram-init \
        util-linux \
        coreutils \
        grep \
        sed
}

# Set hostname
set_hostname() {
    log_info "Setting hostname to $HOSTNAME..."
    echo "$HOSTNAME" > /etc/hostname
    hostname -F /etc/hostname
}

# Set timezone
set_timezone() {
    log_info "Setting timezone to $TIMEZONE..."
    cp "/usr/share/zoneinfo/$TIMEZONE" /etc/localtime
    echo "$TIMEZONE" > /etc/timezone
}

# Configure doas
configure_doas() {
    log_info "Configuring doas..."
    mkdir -p /etc/doas.d
    echo "permit persist :wheel" > /etc/doas.d/doas.conf
    chmod 0400 /etc/doas.d/doas.conf
    log_success "doas configured"
}

# Create admin user
create_admin_user() {
    log_info "Creating admin user: $ADMIN_USER..."
    
    if id "$ADMIN_USER" &>/dev/null; then
        log_warning "User $ADMIN_USER already exists"
        return
    fi
    
    adduser -D -G wheel "$ADMIN_USER"
    log_success "Admin user created"
    
    if [[ "$SKIP_INTERACTIVE" != true ]]; then
        log_warning "Set password for $ADMIN_USER:"
        passwd "$ADMIN_USER"
    fi
}

# Configure SSH
configure_ssh() {
    log_info "Configuring SSH security..."
    
    # Disable root login
    sed -i 's/#PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
    
    # Ensure password authentication is enabled initially
    sed -i 's/#PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
    
    # Enable service
    rc-update add sshd default
    rc-service sshd restart
    
    log_success "SSH configured and restarted"
}

# Prepare USB device
prepare_usb_device() {
    log_info "Preparing USB device: $USB_DEVICE..."
    
    # Unmount if mounted
    if grep -q "$USB_DEVICE" /proc/mounts; then
        log_warning "USB device is mounted, unmounting..."
        umount "${USB_DEVICE}"* || true
    fi
    
    # Create GPT partition table
    log_info "Creating GPT partition table..."
    parted "$USB_DEVICE" --script mklabel gpt
    
    # Create ext4 partition
    log_info "Creating ext4 partition..."
    parted "$USB_DEVICE" --script mkpart primary ext4 0% 100%
    
    # Wait for device to settle
    sleep 2
    
    # Format partition
    log_info "Formatting partition..."
    mkfs.ext4 -F -L dockerdata "${USB_DEVICE}1"
    
    log_success "USB device prepared"
}

# Configure Docker storage
configure_docker_storage() {
    log_info "Configuring Docker storage on USB..."
    
    # Create mount point
    mkdir -p /mnt/docker
    
    # Mount USB
    mount "${USB_DEVICE}1" /mnt/docker
    
    # Get UUID for permanent mount
    USB_UUID=$(blkid -s UUID -o value "${USB_DEVICE}1")
    
    # Add to fstab if not already present
    if ! grep -q "$USB_UUID" /etc/fstab; then
        echo "UUID=$USB_UUID /mnt/docker ext4 defaults,noatime 0 2" >> /etc/fstab
    fi
    
    # Create Docker storage directory
    mkdir -p /mnt/docker/docker
    
    # Stop Docker service
    rc-service docker stop || true
    
    # Move existing Docker data
    if [[ -d /var/lib/docker ]] && [[ -n "$(ls -A /var/lib/docker)" ]]; then
        log_info "Moving existing Docker data..."
        mv /var/lib/docker/* /mnt/docker/docker/ 2>/dev/null || true
    fi
    
    # Replace Docker directory with bind mount
    rm -rf /var/lib/docker
    mkdir -p /var/lib/docker
    
    # Bind mount Docker directory
    mount --bind /mnt/docker/docker /var/lib/docker
    
    # Add bind mount to fstab if not already present
    if ! grep -q "/var/lib/docker" /etc/fstab; then
        echo "/mnt/docker/docker /var/lib/docker none bind 0 0" >> /etc/fstab
    fi
    
    log_success "Docker storage configured"
}

# Enable and test Docker
enable_docker() {
    log_info "Enabling Docker service..."
    
    rc-update add docker default
    rc-service docker start
    
    # Add admin user to docker group
    addgroup "$ADMIN_USER" docker || true
    
    # Test Docker
    log_info "Testing Docker..."
    if docker run --rm hello-world > /dev/null; then
        log_success "Docker is working correctly"
    else
        log_error "Docker test failed"
        return 1
    fi
}

# Configure Docker daemon
configure_docker_daemon() {
    log_info "Configuring Docker daemon..."
    
    mkdir -p /etc/docker
    
    cat > /etc/docker/daemon.json <<EOF
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  }
}
EOF
    
    rc-service docker restart
    log_success "Docker daemon configured"
}

# Configure zram
configure_zram() {
    log_info "Configuring zram swap..."
    
    # Configure zram for swap only
    cat > /etc/conf.d/zram-init <<EOF
num_devices=1
type0=swap
size0=$ZRAM_SIZE
EOF
    
    # Disable physical swap in fstab
    if grep -q "swap" /etc/fstab; then
        sed -i 's/^/#/' /etc/fstab | grep swap || true
    fi
    
    # Enable and start zram service
    rc-update add zram-init default
    rc-service zram-init restart
    
    # Verify zram configuration
    log_info "Verifying zram configuration..."
    sleep 1
    swapon --show
    
    log_success "zram swap configured"
}

# Summary
print_summary() {
    cat <<EOF

${GREEN}================================
Alpine Setup Complete!
================================${NC}

System Configuration:
  Hostname:     $HOSTNAME
  Admin user:   $ADMIN_USER
  Timezone:     $TIMEZONE
  USB device:   $USB_DEVICE

Docker Configuration:
  Storage:      /mnt/docker
  Data path:    /var/lib/docker
  Logging:      JSON (10MB per file, 3 files)

Next Steps:
  1. Log in as admin user: ${ADMIN_USER}
  2. Test Docker: docker ps
  3. Check storage: df -h
  4. Monitor system: htop

To install KVM-over-IP:
  1. Clone repository: git clone <repo-url>
  2. Navigate to directory
  3. Run installation script

Documentation:
  - Alpine: https://alpinelinux.org
  - Docker: https://docs.docker.com
  - KVM-over-IP: Check project README

${YELLOW}Important${NC}:
  - SSH root login is disabled
  - Use '${ADMIN_USER}' user for SSH
  - Change SSH to key-based auth in production
  - Monitor USB drive health regularly

EOF
}

# Main execution
main() {
    log_info "Alpine Linux + Docker Setup for KVM-over-IP"
    log_info "============================================"
    
    parse_arguments "$@"
    check_root
    detect_usb_device
    confirm_configuration
    
    # Execute setup steps
    log_info "Starting setup process..."
    update_system
    enable_community_repo
    install_packages
    set_hostname
    set_timezone
    configure_doas
    create_admin_user
    configure_ssh
    prepare_usb_device
    configure_docker_storage
    enable_docker
    configure_docker_daemon
    configure_zram
    
    log_success "All setup steps completed!"
    print_summary
}

# Run main function with all arguments
main "$@"
