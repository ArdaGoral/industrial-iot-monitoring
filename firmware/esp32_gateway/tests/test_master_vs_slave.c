/*
 * Karşılıklı test: ESP32'deki Modbus MASTER kodu ile STM32'deki Modbus SLAVE kodu
 * aynı programda birbirine bağlanıyor. Master isteği üretir, slave cevaplar,
 * master cevabı çözer. Donanım ya da emülatör gerekmez.
 *
 * Derle ve çalıştır (esp32_gateway klasöründeyken):
 *   clang -Wall -Wextra -Imain -I../bluepill_rtos/include -o tests/test_master_vs_slave \
 *     tests/test_master_vs_slave.c main/modbus_proto.c ../bluepill_rtos/src/modbus.c \
 *     && ./tests/test_master_vs_slave
 */
#include <stdio.h>
#include <string.h>
#include "modbus_proto.h"   /* master (ESP32) */
#include "modbus.h"         /* slave  (STM32) */

static int failures = 0;

#define CHECK(cond, name) do { \
    if (cond) printf("  OK    %s\n", name); \
    else { printf("  HATA  %s  (satir %d)\n", name, __LINE__); failures++; } \
} while (0)

int main(void)
{
    mb_regs_t slave_regs = {
        .input   = { 364, 662, 166, 5455, 5007, 42, 0 },
        .holding = { 0, 7000 },
    };
    uint8_t req[MBM_MAX_FRAME], resp[MBM_MAX_FRAME];
    uint16_t vals[16];
    uint8_t ex = 0;
    size_t n, m;

    printf("CRC iki tarafta ayni\n");
    {
        const uint8_t p[] = { 0x01, 0x03, 0x00, 0x00, 0x00, 0x0A };
        CHECK(mbm_crc16(p, 6) == mb_crc16(p, 6), "master CRC == slave CRC");
    }

    printf("FC04: 7 input register oku\n");
    n = mbm_build_read(req, 1, 0x04, 0, 7);
    m = mb_process(1, req, n, &slave_regs, resp);
    CHECK(mbm_parse_read(resp, m, 1, 0x04, 7, vals, &ex) == MBM_OK, "cevap gecerli");
    CHECK(vals[0] == 364 && vals[3] == 5455 && vals[5] == 42, "degerler dogru");

    printf("FC03: holding register oku\n");
    n = mbm_build_read(req, 1, 0x03, 0, 2);
    m = mb_process(1, req, n, &slave_regs, resp);
    CHECK(mbm_parse_read(resp, m, 1, 0x03, 2, vals, &ex) == MBM_OK && vals[1] == 7000,
          "alarm esigi 7000");

    printf("FC06: roleyi ac\n");
    n = mbm_build_write_single(req, 1, MB_HR_RELAY, 1);
    m = mb_process(1, req, n, &slave_regs, resp);
    CHECK(mbm_parse_write_single(resp, m, req, &ex) == MBM_OK, "yazma onaylandi");
    CHECK(slave_regs.holding[MB_HR_RELAY] == 1, "slave'de role = 1");

    printf("Istisnalar\n");
    n = mbm_build_write_single(req, 1, MB_HR_RELAY, 5);
    m = mb_process(1, req, n, &slave_regs, resp);
    CHECK(mbm_parse_write_single(resp, m, req, &ex) == MBM_ERR_EXCEPTION && ex == 3,
          "role'ye 5 -> istisna 03");

    n = mbm_build_read(req, 1, 0x04, 5, 10);
    m = mb_process(1, req, n, &slave_regs, resp);
    CHECK(mbm_parse_read(resp, m, 1, 0x04, 10, vals, &ex) == MBM_ERR_EXCEPTION && ex == 2,
          "tablo disi okuma -> istisna 02");

    printf("Bozuk ve yanlis cevaplar\n");
    n = mbm_build_read(req, 1, 0x04, 0, 2);
    m = mb_process(1, req, n, &slave_regs, resp);
    resp[4] ^= 0x01;
    CHECK(mbm_parse_read(resp, m, 1, 0x04, 2, vals, &ex) == MBM_ERR_CRC, "bozuk cevap -> crc");

    n = mbm_build_read(req, 1, 0x04, 0, 2);
    m = mb_process(1, req, n, &slave_regs, resp);
    CHECK(mbm_parse_read(resp, m, 1, 0x04, 3, vals, &ex) == MBM_ERR_LENGTH,
          "beklenenden kisa cevap -> uzunluk");
    CHECK(mbm_parse_read(resp, m, 2, 0x04, 2, vals, &ex) == MBM_ERR_SLAVE,
          "baska cihazdan cevap -> yanlis cihaz");

    n = mbm_build_read(req, 7, 0x04, 0, 2);
    CHECK(mb_process(1, req, n, &slave_regs, resp) == 0,
          "slave 7'ye giden istege slave 1 cevap vermez (master zaman asimi yasar)");

    printf("\n%s (%d hata)\n", failures ? "BASARISIZ" : "TUM TESTLER GECTI", failures);
    return failures ? 1 : 0;
}
