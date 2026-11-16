/*
 * Modbus RTU master - protokol mantığı (donanımdan bağımsız)
 */
#include <string.h>
#include "modbus_proto.h"

uint16_t mbm_crc16(const uint8_t *data, size_t len)
{
    uint16_t crc = 0xFFFF;
    for (size_t i = 0; i < len; i++) {
        crc ^= data[i];
        for (int bit = 0; bit < 8; bit++)
            crc = (crc & 1) ? (uint16_t)((crc >> 1) ^ 0xA001) : (uint16_t)(crc >> 1);
    }
    return crc;
}

static size_t append_crc(uint8_t *f, size_t len)
{
    uint16_t c = mbm_crc16(f, len);
    f[len]     = (uint8_t)(c & 0xFF);     /* önce düşük bayt */
    f[len + 1] = (uint8_t)(c >> 8);
    return len + 2;
}

static int crc_ok(const uint8_t *f, size_t len)
{
    if (len < 4) return 0;
    uint16_t c = mbm_crc16(f, len - 2);
    return f[len - 2] == (uint8_t)(c & 0xFF) && f[len - 1] == (uint8_t)(c >> 8);
}

static size_t build_6(uint8_t *out, uint8_t slave, uint8_t fc, uint16_t a, uint16_t b)
{
    out[0] = slave;
    out[1] = fc;
    out[2] = (uint8_t)(a >> 8); out[3] = (uint8_t)a;    /* büyük bayt önce */
    out[4] = (uint8_t)(b >> 8); out[5] = (uint8_t)b;
    return append_crc(out, 6);
}

size_t mbm_build_read(uint8_t *out, uint8_t slave, uint8_t fc, uint16_t start, uint16_t qty)
{
    return build_6(out, slave, fc, start, qty);
}

size_t mbm_build_write_single(uint8_t *out, uint8_t slave, uint16_t addr, uint16_t value)
{
    return build_6(out, slave, 0x06, addr, value);
}

/* İstisna cevabı mı? (fonksiyon kodunun en üst biti 1) */
static int check_exception(const uint8_t *resp, size_t len, uint8_t slave, uint8_t fc,
                           uint8_t *ex_code, mbm_result_t *res)
{
    if (len == MBM_EXCEPTION_LEN && resp[1] == (uint8_t)(fc | 0x80)) {
        if (!crc_ok(resp, len))      *res = MBM_ERR_CRC;
        else if (resp[0] != slave)   *res = MBM_ERR_SLAVE;
        else { if (ex_code) *ex_code = resp[2]; *res = MBM_ERR_EXCEPTION; }
        return 1;
    }
    return 0;
}

mbm_result_t mbm_parse_read(const uint8_t *resp, size_t len, uint8_t slave, uint8_t fc,
                            uint16_t qty, uint16_t *out, uint8_t *ex_code)
{
    mbm_result_t r;
    if (check_exception(resp, len, slave, fc, ex_code, &r)) return r;

    if (len != mbm_read_response_len(qty)) return MBM_ERR_LENGTH;
    if (!crc_ok(resp, len))                return MBM_ERR_CRC;
    if (resp[0] != slave)                  return MBM_ERR_SLAVE;
    if (resp[1] != fc)                     return MBM_ERR_FUNCTION;
    if (resp[2] != qty * 2)                return MBM_ERR_LENGTH;

    for (uint16_t i = 0; i < qty; i++)
        out[i] = (uint16_t)((resp[3 + 2 * i] << 8) | resp[4 + 2 * i]);
    return MBM_OK;
}

mbm_result_t mbm_parse_write_single(const uint8_t *resp, size_t len, const uint8_t *req,
                                    uint8_t *ex_code)
{
    mbm_result_t r;
    if (check_exception(resp, len, req[0], 0x06, ex_code, &r)) return r;

    if (len != MBM_WRITE_RESPONSE_LEN) return MBM_ERR_LENGTH;
    if (!crc_ok(resp, len))            return MBM_ERR_CRC;
    if (memcmp(resp, req, 6) != 0)     return MBM_ERR_FUNCTION;
    return MBM_OK;
}

const char *mbm_result_str(mbm_result_t r)
{
    switch (r) {
    case MBM_OK:            return "ok";
    case MBM_ERR_CRC:       return "crc";
    case MBM_ERR_SLAVE:     return "yanlis_cihaz";
    case MBM_ERR_FUNCTION:  return "yanlis_fonksiyon";
    case MBM_ERR_LENGTH:    return "yanlis_uzunluk";
    case MBM_ERR_EXCEPTION: return "istisna";
    case MBM_ERR_TIMEOUT:   return "zaman_asimi";
    default:                return "bilinmeyen";
    }
}
