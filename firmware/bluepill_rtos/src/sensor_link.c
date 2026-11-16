/*
 * Sensör hattı çerçeve çözücü (donanımdan bağımsız durum makinesi)
 */
#include "sensor_link.h"
#include "modbus.h"      /* mb_crc16: Modbus ile aynı CRC'yi kullanıyoruz */

enum { WAIT_H0 = 0, WAIT_H1, COLLECT };

void sl_init(sl_parser_t *p)
{
    p->state = WAIT_H0;
    p->pos = 0;
    p->crc_errors = 0;
}

int sl_feed(sl_parser_t *p, uint8_t b, uint16_t values[SL_VALUE_COUNT])
{
    switch (p->state) {
    case WAIT_H0:                        /* çerçeve başlangıcını ara */
        if (b == SL_HEADER0) {
            p->buf[0] = b;
            p->state = WAIT_H1;
        }
        return 0;

    case WAIT_H1:
        if (b == SL_HEADER1) {
            p->buf[1] = b;
            p->pos = 2;
            p->state = COLLECT;
        } else if (b != SL_HEADER0) {    /* AA AA 55 gibi durumlarda AA'da kal */
            p->state = WAIT_H0;
        }
        return 0;

    case COLLECT:
    default:
        p->buf[p->pos++] = b;
        if (p->pos < SL_FRAME_LEN)
            return 0;

        p->state = WAIT_H0;              /* her durumda baştan başla */
        {
            uint16_t calc = mb_crc16(p->buf, SL_FRAME_LEN - 2);
            uint16_t recv = (uint16_t)(p->buf[SL_FRAME_LEN - 2] |
                                       (p->buf[SL_FRAME_LEN - 1] << 8));
            if (calc != recv) {
                p->crc_errors++;
                return 0;
            }
        }
        for (int i = 0; i < SL_VALUE_COUNT; i++)
            values[i] = (uint16_t)((p->buf[2 + 2 * i] << 8) | p->buf[3 + 2 * i]);
        return 1;
    }
}

size_t sl_encode(const uint16_t values[SL_VALUE_COUNT], uint8_t out[SL_FRAME_LEN])
{
    out[0] = SL_HEADER0;
    out[1] = SL_HEADER1;
    for (int i = 0; i < SL_VALUE_COUNT; i++) {
        out[2 + 2 * i]     = (uint8_t)(values[i] >> 8);
        out[3 + 2 * i]     = (uint8_t)(values[i] & 0xFF);
    }
    uint16_t crc = mb_crc16(out, SL_FRAME_LEN - 2);
    out[SL_FRAME_LEN - 2] = (uint8_t)(crc & 0xFF);
    out[SL_FRAME_LEN - 1] = (uint8_t)(crc >> 8);
    return SL_FRAME_LEN;
}

size_t sl_encode_status(uint8_t relay, uint8_t flags, uint8_t out[SL_STATUS_LEN])
{
    out[0] = SL_HEADER0;
    out[1] = SL_STATUS_HEADER1;
    out[2] = relay;
    out[3] = flags;
    uint16_t crc = mb_crc16(out, 4);
    out[4] = (uint8_t)(crc & 0xFF);
    out[5] = (uint8_t)(crc >> 8);
    return SL_STATUS_LEN;
}
