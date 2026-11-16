/*
 * Modbus RTU slave - protokol mantığı (donanımdan bağımsız)
 *
 * Desteklenen fonksiyon kodları:
 *   FC03 Read Holding Registers
 *   FC04 Read Input Registers
 *   FC06 Write Single Register
 */
#include "modbus.h"

uint16_t mb_crc16(const uint8_t *data, size_t len)
{
    uint16_t crc = 0xFFFF;
    for (size_t i = 0; i < len; i++) {
        crc ^= data[i];
        for (int bit = 0; bit < 8; bit++) {
            crc = (crc & 0x0001) ? (uint16_t)((crc >> 1) ^ 0xA001) : (uint16_t)(crc >> 1);
        }
    }
    return crc;
}

/* Paketin sonuna CRC ekler (önce düşük bayt), toplam uzunluğu döndürür */
static size_t append_crc(uint8_t *frame, size_t len)
{
    uint16_t crc = mb_crc16(frame, len);
    frame[len]     = (uint8_t)(crc & 0xFF);
    frame[len + 1] = (uint8_t)(crc >> 8);
    return len + 2;
}

/* Modbus'ta 16 bitlik sayılar büyük bayt önce (big-endian) gelir */
static uint16_t get_u16(const uint8_t *p)
{
    return (uint16_t)((p[0] << 8) | p[1]);
}

static size_t exception(uint8_t slave_id, uint8_t fc, uint8_t code, uint8_t *resp)
{
    resp[0] = slave_id;
    resp[1] = (uint8_t)(fc | 0x80);    /* en üst bit 1 = "bu bir hata cevabı" */
    resp[2] = code;
    return append_crc(resp, 3);
}

static size_t read_registers(uint8_t slave_id, uint8_t fc, const uint8_t *req,
                             const uint16_t *table, uint16_t table_size, uint8_t *resp)
{
    uint16_t start = get_u16(&req[2]);
    uint16_t qty   = get_u16(&req[4]);

    if (qty < 1 || qty > 125)
        return exception(slave_id, fc, MB_EX_ILLEGAL_VALUE, resp);
    if ((uint32_t)start + qty > table_size)
        return exception(slave_id, fc, MB_EX_ILLEGAL_ADDRESS, resp);

    resp[0] = slave_id;
    resp[1] = fc;
    resp[2] = (uint8_t)(qty * 2);      /* kaç bayt veri geliyor */
    for (uint16_t i = 0; i < qty; i++) {
        uint16_t v = table[start + i];
        resp[3 + 2 * i]     = (uint8_t)(v >> 8);
        resp[3 + 2 * i + 1] = (uint8_t)(v & 0xFF);
    }
    return append_crc(resp, 3 + (size_t)qty * 2);
}

static size_t write_single(uint8_t slave_id, const uint8_t *req, mb_regs_t *regs, uint8_t *resp)
{
    uint16_t addr  = get_u16(&req[2]);
    uint16_t value = get_u16(&req[4]);

    if (addr >= MB_HOLDING_COUNT)
        return exception(slave_id, 0x06, MB_EX_ILLEGAL_ADDRESS, resp);
    if (addr == MB_HR_RELAY && value > 1)
        return exception(slave_id, 0x06, MB_EX_ILLEGAL_VALUE, resp);
    /* Koruma kilidi: motor aşırı ısındığı için PLC röleyi düşürdü. Uzaktan gelen
     * bir komut bu kararı geçersiz kılamaz; motor soğuyana kadar reddet. */
    if (addr == MB_HR_RELAY && value == 1 && regs->relay_locked)
        return exception(slave_id, 0x06, MB_EX_DEVICE_FAILURE, resp);

    regs->holding[addr] = value;

    /* FC06'nın cevabı isteğin aynısıdır (yazıldığının onayı) */
    for (int i = 0; i < 6; i++) resp[i] = req[i];
    return append_crc(resp, 6);
}

size_t mb_process(uint8_t slave_id, const uint8_t *req, size_t len,
                  mb_regs_t *regs, uint8_t *resp)
{
    if (len < 4 || len > MB_MAX_FRAME)
        return 0;

    /* 1) CRC kontrolü: bozuk paketlere Modbus kuralı gereği hiç cevap verilmez */
    uint16_t calc = mb_crc16(req, len - 2);
    uint16_t recv = (uint16_t)(req[len - 2] | (req[len - 1] << 8));
    if (calc != recv)
        return 0;

    /* 2) Paket bize mi? Hattaki diğer cihazlara gelenleri sessizce yok say */
    if (req[0] != slave_id)
        return 0;

    uint8_t fc = req[1];
    switch (fc) {
    case 0x03:
        if (len != 8) return 0;
        return read_registers(slave_id, fc, req, regs->holding, MB_HOLDING_COUNT, resp);
    case 0x04:
        if (len != 8) return 0;
        return read_registers(slave_id, fc, req, regs->input, MB_INPUT_COUNT, resp);
    case 0x06:
        if (len != 8) return 0;
        return write_single(slave_id, req, regs, resp);
    default:
        return exception(slave_id, fc, MB_EX_ILLEGAL_FUNCTION, resp);
    }
}
