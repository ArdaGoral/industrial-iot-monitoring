/*
 * Faz 2 - Adım 1: Modbus RTU CRC-16
 *
 * Her Modbus RTU paketinin sonunda 2 baytlık bir CRC (hata kontrol değeri) olur.
 * Gönderen paketi hesaplar ve sona ekler. Alan aynı hesabı tekrar yapar:
 * sonuç tutmuyorsa paket yolda bozulmuştur, çöpe atılır.
 *
 * Algoritma: CRC-16/MODBUS
 *   - Başlangıç değeri: 0xFFFF
 *   - Polinom: 0xA001 (0x8005'in bitleri ters çevrilmiş hali)
 *   - Sonuç pakete önce DÜŞÜK bayt, sonra YÜKSEK bayt olarak eklenir
 *
 * Derle ve çalıştır:
 *   clang -Wall -Wextra -o crc16 crc16.c && ./crc16
 */

#include <stdio.h>
#include <stdint.h>
#include <stddef.h>

/* data: byte dizisinin başlangıç adresi, len: kaç bayt */
uint16_t modbus_crc16(const uint8_t *data, size_t len)
{
    uint16_t crc = 0xFFFF;

    for (size_t i = 0; i < len; i++) {
        crc ^= data[i];                       /* yeni baytı CRC'nin alt 8 bitine XOR'la */

        for (int bit = 0; bit < 8; bit++) {   /* baytın 8 biti için tekrarla */
            if (crc & 0x0001) {               /* en sağdaki bit 1 mi? */
                crc = (crc >> 1) ^ 0xA001;    /* sağa kaydır, polinomla XOR'la */
            } else {
                crc = crc >> 1;               /* sadece sağa kaydır */
            }
        }
    }
    return crc;
}

/* Paketin son 2 baytındaki CRC doğru mu? 1 = geçerli, 0 = bozuk */
int modbus_frame_valid(const uint8_t *frame, size_t len)
{
    if (len < 4) return 0;                    /* en kısa geçerli paket 4 bayt */

    uint16_t calc = modbus_crc16(frame, len - 2);
    uint16_t recv = (uint16_t)(frame[len - 2] | (frame[len - 1] << 8));  /* düşük + yüksek */
    return calc == recv;
}

static void print_frame(const char *label, const uint8_t *f, size_t len)
{
    printf("%-22s", label);
    for (size_t i = 0; i < len; i++) printf("%02X ", f[i]);
    printf("\n");
}

int main(void)
{
    /*
     * Örnek istek: "1 numaralı cihazın 0. register'ından başlayarak 10 register oku"
     *   01    -> slave adresi
     *   03    -> fonksiyon kodu (Read Holding Registers)
     *   00 00 -> başlangıç adresi
     *   00 0A -> kaç register (10)
     */
    uint8_t request[8] = { 0x01, 0x03, 0x00, 0x00, 0x00, 0x0A, 0, 0 };

    uint16_t crc = modbus_crc16(request, 6);
    request[6] = crc & 0xFF;          /* düşük bayt */
    request[7] = (crc >> 8) & 0xFF;   /* yüksek bayt */

    printf("Hesaplanan CRC: 0x%04X\n", crc);
    print_frame("Gönderilen paket:", request, 8);
    printf("Beklenen       : 01 03 00 00 00 0A C5 CD\n\n");

    printf("Paket geçerli mi?      %s\n", modbus_frame_valid(request, 8) ? "EVET" : "HAYIR");

    /* Yolda 1 bitin bozulduğunu taklit et */
    request[5] ^= 0x01;
    print_frame("Bozulmuş paket:", request, 8);
    printf("Paket geçerli mi?      %s\n", modbus_frame_valid(request, 8) ? "EVET" : "HAYIR");

    return 0;
}
