/*
 * sensor_link.c için birim testleri - Mac'te çalışır.
 *
 *   clang -Wall -Wextra -Iinclude -o tests/test_sensor_link \
 *         tests/test_sensor_link.c src/sensor_link.c src/modbus.c && ./tests/test_sensor_link
 */
#include <stdio.h>
#include <string.h>
#include "sensor_link.h"

static int failures = 0;

#define CHECK(cond, name) do { \
    if (cond) printf("  OK    %s\n", name); \
    else { printf("  HATA  %s  (satir %d)\n", name, __LINE__); failures++; } \
} while (0)

/* Bir bayt dizisini çözücüye besler, kaç geçerli çerçeve çıktığını sayar */
static int feed_all(sl_parser_t *p, const uint8_t *data, size_t n, uint16_t out[SL_VALUE_COUNT])
{
    int frames = 0;
    for (size_t i = 0; i < n; i++)
        frames += sl_feed(p, data[i], out);
    return frames;
}

int main(void)
{
    const uint16_t in[SL_VALUE_COUNT] = { 364, 662, 166, 5455, 5007 };
    uint16_t out[SL_VALUE_COUNT];
    uint8_t frame[SL_FRAME_LEN];
    sl_parser_t p;

    printf("Kodlama\n");
    size_t n = sl_encode(in, frame);
    CHECK(n == 14, "cerceve 14 bayt");
    CHECK(frame[0] == 0xAA && frame[1] == 0x55, "baslik AA 55");
    CHECK(frame[2] == 0x01 && frame[3] == 0x6C, "ilk deger 364 = 01 6C (buyuk bayt once)");

    printf("Temiz cerceve\n");
    sl_init(&p);
    memset(out, 0, sizeof(out));
    CHECK(feed_all(&p, frame, n, out) == 1, "1 cerceve cozuldu");
    CHECK(memcmp(in, out, sizeof(in)) == 0, "degerler aynen geri geldi");

    printf("Basinda cop veri\n");
    {
        uint8_t buf[5 + SL_FRAME_LEN] = { 0x12, 0xAA, 0x00, 0x55, 0xAA };   /* yaniltici baslik parcalari */
        memcpy(buf + 5, frame, n);
        sl_init(&p);
        CHECK(feed_all(&p, buf, sizeof(buf), out) == 1, "cop veriden sonra cerceve bulundu");
    }

    printf("Arka arkaya iki cerceve\n");
    {
        uint8_t buf[2 * SL_FRAME_LEN];
        memcpy(buf, frame, n);
        memcpy(buf + n, frame, n);
        sl_init(&p);
        CHECK(feed_all(&p, buf, sizeof(buf), out) == 2, "2 cerceve cozuldu");
    }

    printf("Bozuk cerceve\n");
    {
        uint8_t bad[SL_FRAME_LEN];
        memcpy(bad, frame, n);
        bad[8] ^= 0x04;                                  /* yolda 1 bit bozuldu */
        sl_init(&p);
        CHECK(feed_all(&p, bad, n, out) == 0, "bozuk cerceve kabul edilmedi");
        CHECK(p.crc_errors == 1, "CRC hatasi sayildi");
        CHECK(feed_all(&p, frame, n, out) == 1, "ardindan gelen saglam cerceve cozuldu");
    }

    printf("\n%s (%d hata)\n", failures ? "BASARISIZ" : "TUM TESTLER GECTI", failures);
    return failures ? 1 : 0;
}
