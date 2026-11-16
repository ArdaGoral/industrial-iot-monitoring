/*
 * Sensör hattı: sensör modülünden USART3 üzerinden gelen veri çerçeveleri.
 *
 * Çerçeve formatı (14 bayt):
 *   AA 55 | 5 x uint16 (büyük bayt önce) | CRC-16 (küçük bayt önce)
 *
 *   değer sırası: vib_rms, vib_peak, kurtosis, sıcaklık, akım
 *   (Modbus input register 0..4 ile aynı sıra ve aynı ölçekler)
 *
 * Bu modül de donanımdan bağımsızdır: bayt bayt beslenen bir durum makinesi.
 */
#ifndef SENSOR_LINK_H
#define SENSOR_LINK_H

#include <stdint.h>
#include <stddef.h>

#define SL_HEADER0      0xAA
#define SL_HEADER1      0x55
#define SL_VALUE_COUNT  5
#define SL_FRAME_LEN    (2 + SL_VALUE_COUNT * 2 + 2)    /* 14 bayt */

typedef struct {
    uint8_t  state;              /* 0: AA bekle, 1: 55 bekle, 2: veri topla */
    uint8_t  pos;
    uint8_t  buf[SL_FRAME_LEN];
    uint32_t crc_errors;         /* CRC'si tutmayan çerçeve sayısı */
} sl_parser_t;

void sl_init(sl_parser_t *p);

/* Bir bayt besler. Geçerli bir çerçeve tamamlandığında 1 döner ve
 * değerleri `values` dizisine yazar; aksi halde 0 döner. */
int sl_feed(sl_parser_t *p, uint8_t byte, uint16_t values[SL_VALUE_COUNT]);

/* Değerlerden çerçeve üretir (testlerde ve formatın belgesi olarak) */
size_t sl_encode(const uint16_t values[SL_VALUE_COUNT], uint8_t out[SL_FRAME_LEN]);

/*
 * Ters yön: PLC -> sensör/motor modülü durum çerçevesi (6 bayt)
 *   AA 66 | röle (0/1) | durum bitleri (MB_IR_STATUS'un alt baytı) | CRC-16 (küçük bayt önce)
 *
 * Gerçek bir sahada kontaktör motoru doğrudan keser; bizde "motor" simülatörün
 * içinde olduğu için röle durumunu ona bu çerçeveyle bildiriyoruz.
 * PLC her sensör çerçevesine bir durum çerçevesiyle cevap verir.
 */
#define SL_STATUS_HEADER1  0x66
#define SL_STATUS_LEN      6

size_t sl_encode_status(uint8_t relay, uint8_t flags, uint8_t out[SL_STATUS_LEN]);

#endif /* SENSOR_LINK_H */
