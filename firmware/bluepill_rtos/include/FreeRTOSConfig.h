/*
 * FreeRTOS ayarları - STM32F103 (Cortex-M3), dahili 8 MHz saat
 */
#ifndef FREERTOS_CONFIG_H
#define FREERTOS_CONFIG_H

#if defined(__GNUC__) && !defined(__ASSEMBLER__)
#include <stdint.h>
extern uint32_t SystemCoreClock;                 /* CMSIS'in tuttuğu işlemci frekansı */
void vAssertCalled(const char *file, int line);  /* main.c içinde */
#endif

/* --- Zamanlayıcı (scheduler) --- */
#define configUSE_PREEMPTION                    1   /* yüksek öncelikli görev düşük olanı kesebilir */
#define configUSE_PORT_OPTIMISED_TASK_SELECTION 1
#define configCPU_CLOCK_HZ                      ( SystemCoreClock )
#define configTICK_RATE_HZ                      ( ( TickType_t ) 1000 )  /* 1 tick = 1 ms */
#define configMAX_PRIORITIES                    5
#define configIDLE_SHOULD_YIELD                 1
#define configTICK_TYPE_WIDTH_IN_BITS           TICK_TYPE_WIDTH_32_BITS

/* --- Bellek --- */
#define configMINIMAL_STACK_SIZE                ( ( uint16_t ) 128 )     /* word cinsinden */
#define configTOTAL_HEAP_SIZE                   ( ( size_t ) ( 8 * 1024 ) )
#define configSUPPORT_DYNAMIC_ALLOCATION        1
#define configSUPPORT_STATIC_ALLOCATION         0
#define configMAX_TASK_NAME_LEN                 12

/* --- Özellikler --- */
#define configUSE_MUTEXES                       1
#define configUSE_RECURSIVE_MUTEXES             0
#define configUSE_COUNTING_SEMAPHORES           1
#define configQUEUE_REGISTRY_SIZE               0
#define configUSE_TIMERS                        0
#define configUSE_CO_ROUTINES                   0
#define configUSE_IDLE_HOOK                     0
#define configUSE_TICK_HOOK                     0
#define configUSE_TRACE_FACILITY                0

/* --- Hata yakalama --- */
#define configCHECK_FOR_STACK_OVERFLOW          2   /* stack taşmasını yakala */
#define configUSE_MALLOC_FAILED_HOOK            1   /* heap biterse haber ver */
#define configASSERT( x )  if( ( x ) == 0 ) { vAssertCalled( __FILE__, __LINE__ ); }

/* --- Kesme öncelikleri: STM32F1'de 4 öncelik biti var (0 = en yüksek, 15 = en düşük) --- */
#define configPRIO_BITS                               4
#define configLIBRARY_LOWEST_INTERRUPT_PRIORITY       15
#define configLIBRARY_MAX_SYSCALL_INTERRUPT_PRIORITY  5
#define configKERNEL_INTERRUPT_PRIORITY \
    ( configLIBRARY_LOWEST_INTERRUPT_PRIORITY << ( 8 - configPRIO_BITS ) )
#define configMAX_SYSCALL_INTERRUPT_PRIORITY \
    ( configLIBRARY_MAX_SYSCALL_INTERRUPT_PRIORITY << ( 8 - configPRIO_BITS ) )

/* --- Kullanılacak API fonksiyonları --- */
#define INCLUDE_vTaskDelay                      1
#define INCLUDE_xTaskDelayUntil                 1
#define INCLUDE_vTaskDelete                     0
#define INCLUDE_uxTaskGetStackHighWaterMark     1
#define INCLUDE_xTaskGetSchedulerState          1

/* --- FreeRTOS'un kesme fonksiyonlarını CMSIS'in vektör tablosundaki isimlere bağla --- */
#define vPortSVCHandler      SVC_Handler
#define xPortPendSVHandler   PendSV_Handler
#define xPortSysTickHandler  SysTick_Handler

#endif /* FREERTOS_CONFIG_H */
