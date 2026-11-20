# FrostByte — RPM spec for Fedora / RHEL / openSUSE (rpmbuild).
# Build with:  ./packaging/build-rpm.sh   (wraps rpmbuild in a Fedora container)
#
# Version is kept in sync with common/frostbyte_common.py by build-rpm.sh.
%global drv_name acer-wmi-linuwu
%global drv_ver  7.3.0.1

Name:           frostbyte
Version: 1.5.2
Release:        1%{?dist}
Summary:        NitroSense/PredatorSense-style fan & thermal control for Acer laptops

License:        GPL-3.0-only
URL:            https://github.com/VanguardArts/frostbyte
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch

Requires:       dkms
Requires:       kmod
Requires:       python3
Requires:       python3-pyqt6
# DKMS needs kernel headers to build the module on install/kernel updates.
Requires:       kernel-devel

%description
FrostByte is an open-source replacement for Acer's NitroSense utility on
Linux: per-fan manual control, MAX mode, custom fan curves, thermal profiles
with per-power-source memory, battery charge limiter, and a live monitoring
dashboard (PyQt6 GUI + frostctl CLI).

Fan speeds and sensors are driven by the vendored acer-wmi-linuwu kernel
driver, built automatically via DKMS and rebuilt on every kernel update. A
small daemon owns all hardware access and enforces a thermal safety override;
the GUI and CLI talk to it over a local socket. The dedicated NitroSense "N"
key toggles the GUI, as on Windows.

%prep
%setup -q

%install
rm -rf %{buildroot}
install -d %{buildroot}/usr/lib/frostbyte
install -d %{buildroot}/usr/bin
install -d %{buildroot}/usr/lib/systemd/system
install -d %{buildroot}/usr/share/applications
install -d %{buildroot}/usr/share/pixmaps
install -d %{buildroot}/usr/share/frostbyte

install -m644 common/frostbyte_common.py  %{buildroot}/usr/lib/frostbyte/
install -m644 common/frostbyte_gpu.py     %{buildroot}/usr/lib/frostbyte/
install -m755 daemon/frostbyte-daemon.py  %{buildroot}/usr/lib/frostbyte/
install -m755 cli/frostctl.py             %{buildroot}/usr/lib/frostbyte/
install -m755 cli/frostbyte-gpu-run      %{buildroot}/usr/lib/frostbyte/
install -m755 gui/frostbyte_gui.py        %{buildroot}/usr/lib/frostbyte/
install -m755 keyd/frostbyte-keyd.py      %{buildroot}/usr/lib/frostbyte/
install -m644 gui/frostbyte.svg           %{buildroot}/usr/lib/frostbyte/

install -m644 systemd/frostbyte-daemon.service %{buildroot}/usr/lib/systemd/system/
install -m644 systemd/frostbyte-keyd.service   %{buildroot}/usr/lib/systemd/system/
# The in-tree units reference /opt/frostbyte; repoint them at /usr/lib/frostbyte.
sed -i 's|/opt/frostbyte|/usr/lib/frostbyte|g' \
    %{buildroot}/usr/lib/systemd/system/frostbyte-*.service

install -m644 desktop/frostbyte.desktop %{buildroot}/usr/share/applications/
install -m644 gui/frostbyte.svg         %{buildroot}/usr/share/pixmaps/
install -m644 gui/frostbyte.svg %{buildroot}/usr/share/icons/hicolor/scalable/apps/frostbyte.svg
for sz in 16 24 32 48 64 96 128 256 512; do
    install -Dm644 packaging/icons/hicolor/${sz}x${sz}/apps/frostbyte.png \
        %{buildroot}/usr/share/icons/hicolor/${sz}x${sz}/apps/frostbyte.png
done
install -m644 etc/frostbyte.conf        %{buildroot}/usr/share/frostbyte/

cat > %{buildroot}/usr/bin/frostbyte <<'WRAP_EOF'
#!/bin/sh
exec /usr/bin/python3 /usr/lib/frostbyte/frostbyte_gui.py "$@"
WRAP_EOF
chmod 755 %{buildroot}/usr/bin/frostbyte
ln -s ../lib/frostbyte/frostctl.py       %{buildroot}/usr/bin/frostctl
ln -s ../lib/frostbyte/frostbyte-keyd.py %{buildroot}/usr/bin/frostbyte-keyd
ln -s ../lib/frostbyte/frostbyte-gpu-run %{buildroot}/usr/bin/frostbyte-gpu-run

# Vendored kernel driver source for DKMS.
install -d %{buildroot}/usr/src/%{drv_name}-%{drv_ver}
cp -a driver/%{drv_name}/. %{buildroot}/usr/src/%{drv_name}-%{drv_ver}/

%post
if [ -d "/usr/src/%{drv_name}-%{drv_ver}" ] && command -v dkms >/dev/null 2>&1; then
    dkms add    "%{drv_name}/%{drv_ver}" >/dev/null 2>&1 || :
    dkms build  "%{drv_name}/%{drv_ver}" >/dev/null 2>&1 || :
    dkms install "%{drv_name}/%{drv_ver}" >/dev/null 2>&1 || :
fi
modprobe -r acer_wmi 2>/dev/null || :
modprobe acer_wmi 2>/dev/null || :

if [ ! -e /etc/frostbyte/frostbyte.conf ]; then
    mkdir -p /etc/frostbyte
    cp /usr/share/frostbyte/frostbyte.conf /etc/frostbyte/frostbyte.conf || :
fi

if command -v systemctl >/dev/null 2>&1; then
    systemctl daemon-reload || :
    systemctl enable --now frostbyte-daemon.service frostbyte-keyd.service 2>/dev/null || :
fi

%preun
if [ "$1" = "0" ] && command -v systemctl >/dev/null 2>&1; then
    systemctl disable --now frostbyte-daemon.service frostbyte-keyd.service 2>/dev/null || :
fi

%postun
if command -v systemctl >/dev/null 2>&1; then
    systemctl daemon-reload 2>/dev/null || :
fi
if [ "$1" = "0" ]; then
    rm -f /run/frostbyte.sock
    if command -v dkms >/dev/null 2>&1; then
        dkms remove "%{drv_name}/%{drv_ver}" --all >/dev/null 2>&1 || :
    fi
    modprobe -r acer_wmi 2>/dev/null || :
    depmod -a 2>/dev/null || :
fi

%files
/usr/lib/frostbyte
/usr/bin/frostbyte
/usr/bin/frostctl
/usr/bin/frostbyte-keyd
/usr/bin/frostbyte-gpu-run
/usr/lib/systemd/system/frostbyte-daemon.service
/usr/lib/systemd/system/frostbyte-keyd.service
/usr/share/applications/frostbyte.desktop
/usr/share/pixmaps/frostbyte.svg
/usr/share/icons/hicolor/scalable/apps/frostbyte.svg
/usr/share/icons/hicolor/16x16/apps/frostbyte.png
/usr/share/icons/hicolor/24x24/apps/frostbyte.png
/usr/share/icons/hicolor/32x32/apps/frostbyte.png
/usr/share/icons/hicolor/48x48/apps/frostbyte.png
/usr/share/icons/hicolor/64x64/apps/frostbyte.png
/usr/share/icons/hicolor/96x96/apps/frostbyte.png
/usr/share/icons/hicolor/128x128/apps/frostbyte.png
/usr/share/icons/hicolor/256x256/apps/frostbyte.png
/usr/share/icons/hicolor/512x512/apps/frostbyte.png
/usr/share/frostbyte/frostbyte.conf
/usr/src/%{drv_name}-%{drv_ver}

%changelog
* Wed Nov 18 2026 FrostByte Contributors <VanguardArts@users.noreply.github.com> - 1.5.2-1
- Initial RPM packaging; mirrors the Debian package file layout.
