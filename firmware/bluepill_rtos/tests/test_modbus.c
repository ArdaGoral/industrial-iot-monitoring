/*
 * modbus.c için birim testleri - Mac'te çalışır, STM32 gerekmez.
 *
 * Derle ve çalıştır (bluepill_rtos klasöründeyken):
 *   clang -Wall -Wextra -Iinclude -o tests/test_modbus tests/test_modbus.c src/modbus.c && ./tests/test_modbus
 */
#include <stdio.h>
#include <string.h>
#include "modbus.h"

static int failures = 0;

#define CHECK(cond, name) do { \
    if (cond) printf("  OK    %s\n", name); \
    else { printf("  HATA  %s  (satir %d)\n", name, __LINE__); failures++; } \
} while (0)

/* İsteğin ilk 6 baytını alıp CRC ekleyerek tam paket yapar */
static size_t make_request(uint8_t *f, uint8_t id, uint8_t fc, uint16_t a, uint16_t b)
{
    f[0] = id; f[1] = fc;
    f[2] = (uint8_t)(a >> 8); f[3] = (uint8_t)a;
    f[4] = (uint8_t)(b >> 8); f[5] = (uint8_t)b;
    uint16_t crc = mb_crc16(f, 6);
    f[6] = (uint8_t)crc; f[7] = (uint8_t)(crc >> 8);
    return 8;
}

static int crc_ok(const uint8_t *f, size_t n)
{
    uint16_t c = mb_crc16(f, n - 2);
    return f[n - 2] == (uint8_t)c && f[n - 1] == (uint8_t)(c >> 8);
}

int main(void)
{
    mb_regs_t regs = {
        .input   = { 364, 662, 166, 5455, 5007, 42 },
        .holding = { 0, 7000 },
    };
    uint8_t req[MB_MAX_FRAME], resp[MB_MAX_FRAME];
    size_t n, m;

    printf("CRC\n");
    {
        const uint8_t known[] = { 0x01, 0x03, 0x00, 0x00, 0x00, 0x0A };
        CHECK(mb_crc16(known, 6) == 0xCDC5, "bilinen paket CRC = 0xCDC5");
    }

    printf("FC04 input register okuma\n");
    n = make_request(req, 1, 0x04, 0, 6);
    m = mb_process(1, req, n, &regs, resp);
    CHECK(m == 3 + 12 + 2, "cevap uzunlugu 17");
    CHECK(resp[0] == 1 && resp[1] == 0x04 && resp[2] == 12, "baslik: id, fc, bayt sayisi");
    CHECK(resp[3] == 0x01 && resp[4] == 0x6C, "reg0 = 364 (0x016C), buyuk bayt once");
    CHECK(resp[9] == 0x15 && resp[10] == 0x4F, "reg3 = 5455 (0x154F)");
    CHECK(crc_ok(resp, m), "cevabin CRC'si dogru");

    printf("FC04 tablo disi adres\n");
    n = make_request(req, 1, 0x04, 4, 5);          /* 4..8, tablo 6 eleman */
    m = mb_process(1, req, n, &regs, resp);
    CHECK(m == 5 && resp[1] == 0x84 && resp[2] == MB_EX_ILLEGAL_ADDRESS, "istisna 0x84 / kod 02");
    CHECK(crc_ok(resp, m), "istisna cevabinin CRC'si dogru");

    printf("FC04 miktar = 0\n");
    n = make_request(req, 1, 0x04, 0, 0);
    m = mb_process(1, req, n, &regs, resp);
    CHECK(m == 5 && resp[2] == MB_EX_ILLEGAL_VALUE, "istisna kod 03");

    printf("FC03 holding register okuma\n");
    n = make_request(req, 1, 0x03, 0, 2);
    m = mb_process(1, req, n, &regs, resp);
    CHECK(m == 3 + 4 + 2, "cevap uzunlugu 9");
    CHECK(resp[5] == 0x1B && resp[6] == 0x58, "alarm esigi = 7000 (0x1B58)");

    printf("FC06 tek register yazma\n");
    n = make_request(req, 1, 0x06, MB_HR_RELAY, 1);
    m = mb_process(1, req, n, &regs, resp);
    CHECK(m == 8 && memcmp(req, resp, 8) == 0, "cevap istegin aynisi");
    CHECK(regs.holding[MB_HR_RELAY] == 1, "role register'i 1 oldu");

    n = make_request(req, 1, 0x06, MB_HR_RELAY, 5);
    m = mb_process(1, req, n, &regs, resp);
    CHECK(m == 5 && resp[1] == 0x86 && resp[2] == MB_EX_ILLEGAL_VALUE, "role'ye 5 yazmak reddedildi");
    CHECK(regs.holding[MB_HR_RELAY] == 1, "role degeri degismedi");

    n = make_request(req, 1, 0x06, 9, 1);
    m = mb_process(1, req, n, &regs, resp);
    CHECK(m == 5 && resp[2] == MB_EX_ILLEGAL_ADDRESS, "olmayan register'a yazma reddedildi");

    printf("Desteklenmeyen fonksiyon\n");
    n = make_request(req, 1, 0x10, 0, 1);
    m = mb_process(1, req, n, &regs, resp);
    CHECK(m == 5 && resp[1] == 0x90 && resp[2] == MB_EX_ILLEGAL_FUNCTION, "istisna 0x90 / kod 01");

    printf("Sessiz kalinmasi gereken durumlar\n");
    n = make_request(req, 1, 0x04, 0, 6);
    req[3] ^= 0x01;                                /* yolda 1 bit bozuldu */
    CHECK(mb_process(1, req, n, &regs, resp) == 0, "bozuk CRC -> cevap yok");

    n = make_request(req, 7, 0x04, 0, 6);
    CHECK(mb_process(1, req, n, &regs, resp) == 0, "baska cihaza giden paket -> cevap yok");

    CHECK(mb_process(1, req, 3, &regs, resp) == 0, "cok kisa paket -> cevap yok");

    printf("\n%s (%d hata)\n", failures ? "BASARISIZ" : "TUM TESTLER GECTI", failures);
    return failures ? 1 : 0;
}
