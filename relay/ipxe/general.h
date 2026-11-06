/*
 * netdispatch local iPXE build configuration (copied to src/config/local/).
 * iPXE v2 already enables HTTPS, sanboot over HTTP, reboot/poweroff and
 * param commands by default; these add diagnostics useful on site.
 */
#define NSLOOKUP_CMD   /* DNS resolution command */
#define PING_CMD       /* ping command */
#define NTP_CMD        /* ntp command (clock sync for TLS validity checks) */
