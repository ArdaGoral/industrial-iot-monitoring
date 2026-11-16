/*
 * Modbus RTU master - protokol mantığı (donanımdan bağımsız)
 *
 * İstek paketi üretir, gelen cevabı doğrular ve çözer.
 * UART'tan habersizdir; bu sayede Mac'te, STM32'deki slave koduyla
 * karşılıklı test edilebilir.
 */
#ifndef MODBUS_PROTO_H
#define MODBUS_PROTO_H

#include <stdint.h>
#include <stddef.h>

#define MBM_MAX_FRAME 256

typedef enum {
    MBM_OK = 0,
    MBM_ERR_CRC,          /* cevabın CRC'si tutmuyor */
    MBM_ERR_SLAVE,        /* cevap başka cihazdan geldi */
    MBM_ERR_FUNCTION,     /* cevaptaki fonksiyon kodu isteğe uymuyor */
    MBM_ERR_LENGTH,       /* cevap uzunluğu beklenenle uyuşmuyor */
    MBM_ERR_EXCEPTION,    /* slave istisna cevabı döndü (kod ayrıca verilir) */
    MBM_ERR_TIMEOUT,      /* cevap gelmedi (UART katmanı kullanır) */
} mbm_result_t;

uint16_t mbm_crc16(const uint8_t *data, size_t len);

/* FC03/FC04 okuma isteği üretir. Dönüş: paket uzunluğu (8) */
size_t mbm_build_read(uint8_t *out, uint8_t slave, uint8_t fc, uint16_t start, uint16_t qty);

/* FC06 tek register yazma isteği üretir. Dönüş: paket uzunluğu (8) */
size_t mbm_build_write_single(uint8_t *out, uint8_t slave, uint16_t addr, uint16_t value);

/* Okuma cevabının toplam beklenen uzunluğu (istisna değilse) */
static inline size_t mbm_read_response_len(uint16_t qty) { return 3 + (size_t)qty * 2 + 2; }

#define MBM_EXCEPTION_LEN 5
#define MBM_WRITE_RESPONSE_LEN 8

/* Okuma cevabını doğrular ve register değerlerini `out`a yazar.
 * İstisna gelirse MBM_ERR_EXCEPTION döner ve kodu *ex_code'a yazar. */
mbm_result_t mbm_parse_read(const uint8_t *resp, size_t len, uint8_t slave, uint8_t fc,
                            uint16_t qty, uint16_t *out, uint8_t *ex_code);

/* FC06 cevabını doğrular: Modbus'ta cevap, isteğin birebir aynısı olmalı */
mbm_result_t mbm_parse_write_single(const uint8_t *resp, size_t len, const uint8_t *req,
                                    uint8_t *ex_code);

const char *mbm_result_str(mbm_result_t r);

#endif /* MODBUS_PROTO_H */
