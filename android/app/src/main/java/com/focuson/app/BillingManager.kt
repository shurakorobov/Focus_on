package com.focuson.app

import android.app.Activity
import android.content.Context
import android.util.Log
import com.android.billingclient.api.AcknowledgePurchaseParams
import com.android.billingclient.api.BillingClient
import com.android.billingclient.api.BillingClientStateListener
import com.android.billingclient.api.BillingFlowParams
import com.android.billingclient.api.BillingResult
import com.android.billingclient.api.PendingPurchasesParams
import com.android.billingclient.api.ProductDetails
import com.android.billingclient.api.Purchase
import com.android.billingclient.api.PurchasesUpdatedListener
import com.android.billingclient.api.QueryProductDetailsParams
import com.android.billingclient.api.QueryPurchasesParams

/**
 * Обгортка навколо BillingClient для місячної підписки premium.
 *
 * Потік:
 *  1. [startConnection] — асинхронне з'єднання з Google Play (викликати в onStart).
 *  2. [purchasePremium] — запит деталей продукту + launchBillingFlow (нативний UI Google Play).
 *  3. [onPurchasesUpdated] (через PurchasesUpdatedListener) → acknowledge + [onResult].
 *  4. [queryPurchases] — restore вже куплених підписок (викликати після connect + при resume).
 *  5. [endConnection] — у onDestroy.
 *
 * Колбек [onResult] спрацьовує на фінальний результат покупки:
 *   ok=true, token=purchaseToken → JS шле POST /api/play/verify
 *   ok=false, error=повідомлення → JS показує toast.
 *
 * @param onResult (ok: Boolean, purchaseTokenOrError: String) -> Unit
 */
class BillingManager(
    private val context: Context,
    private val skuId: String,
    private val onResult: (ok: Boolean, purchaseTokenOrError: String) -> Unit,
) {
    private val tag = "BillingManager"

    private var billingClient: BillingClient? = null

    /** Деталі продукту кешуємо після першого query — щоб не тягнути щоразу. */
    @Volatile
    private var cachedProductDetails: ProductDetails? = null

    /** true, коли BillingClient готовий до операцій. */
    @Volatile
    private var isReady = false

    /** true, коли триває launchBillingFlow — щоб не запускати двічі. */
    @Volatile
    private var purchaseInProgress = false

    private val purchasesUpdatedListener = PurchasesUpdatedListener { result, purchases ->
        when (result.responseCode) {
            BillingClient.BillingResponseCode.OK -> {
                purchases?.forEach { handlePurchase(it) }
            }
            BillingClient.BillingResponseCode.ITEM_ALREADY_OWNED -> {
                // Користувач вже має підписку — спробуємо restore
                Log.i(tag, "ITEM_ALREADY_OWNED — queryPurchases для restore")
                queryPurchases()
            }
            BillingClient.BillingResponseCode.USER_CANCELED -> {
                purchaseInProgress = false
                onResult(false, "cancelled")
            }
            else -> {
                purchaseInProgress = false
                onResult(false, "error: ${result.responseCode} ${result.debugMessage}")
            }
        }
    }

    fun startConnection() {
        if (billingClient == null) {
            billingClient = BillingClient.newBuilder(context)
                .setListener(purchasesUpdatedListener)
                // Дозволяємо prepaid plans (для підписок) — інакше Play викине例外
                .enablePendingPurchases(
                    PendingPurchasesParams.newBuilder().enablePrepaidPlans().build()
                )
                .build()
        }
        if (billingClient?.isReady == true) {
            isReady = true
            return
        }
        billingClient?.startConnection(object : BillingClientStateListener {
            override fun onBillingSetupFinished(result: BillingResult) {
                if (result.responseCode == BillingClient.BillingResponseCode.OK) {
                    isReady = true
                    Log.i(tag, "BillingClient готовий")
                    // Префетч деталей продукту + restore існуючих підписок
                    queryProductDetails(skuId) { /* кеш заповнено */ }
                    queryPurchases()
                } else {
                    isReady = false
                    Log.w(tag, "BillingSetup failed: ${result.responseCode} ${result.debugMessage}")
                }
            }

            override fun onBillingServiceDisconnected() {
                isReady = false
                Log.w(tag, "BillingClient disconnected — retry на наступному onStart")
            }
        })
    }

    fun endConnection() {
        if (billingClient?.isReady == true) {
            billingClient?.endConnection()
        }
        billingClient = null
        isReady = false
    }

    /**
     * Запускає повний потік покупки: шукає/завантажує ProductDetails → launchBillingFlow.
     * Безпечний для повторних викликів (_purchaseInProgress_ guard).
     * Викликати з UI-потоку (JS-міст).
     */
    fun purchasePremium(activity: Activity) {
        if (purchaseInProgress) {
            Log.w(tag, "purchaseInProgress — ігноруємо повторний виклик")
            return
        }
        if (!isReady) {
            // Спроба реконекту
            startConnection()
            onResult(false, "error: billing not ready, retry")
            return
        }
        val cached = cachedProductDetails
        if (cached != null) {
            launchFlow(activity, cached)
        } else {
            queryProductDetails(skuId) { details ->
                if (details != null) {
                    launchFlow(activity, details)
                } else {
                    onResult(false, "error: product not found")
                }
            }
        }
    }

    private fun launchFlow(activity: Activity, details: ProductDetails) {
        // Для підписок обов'язковий offerToken (беремо базовий offer)
        val offerToken = details.subscriptionOfferDetails
            ?.firstOrNull()?.offerToken
        if (offerToken == null) {
            Log.e(tag, "Немає offerToken для SKU $skuId")
            onResult(false, "error: no offer token")
            return
        }
        val productDetailsParams = BillingFlowParams.ProductDetailsParams.newBuilder()
            .setProductDetails(details)
            .setOfferToken(offerToken)
            .build()
        val flowParams = BillingFlowParams.newBuilder()
            .setProductDetailsParamsList(listOf(productDetailsParams))
            .build()
        purchaseInProgress = true
        val result = billingClient!!.launchBillingFlow(activity, flowParams)
        Log.i(tag, "launchBillingFlow → ${result.responseCode}")
        // Сам результат покупки прийде через purchasesUpdatedListener
    }

    /** Асинхронний query деталей продукту (кешуємо в [cachedProductDetails]). */
    private fun queryProductDetails(sku: String, onDone: (ProductDetails?) -> Unit) {
        val client = billingClient ?: run { onDone(null); return }
        val productList = listOf(
            QueryProductDetailsParams.Product.newBuilder()
                .setProductId(sku)
                .setProductType(BillingClient.ProductType.SUBS)
                .build()
        )
        val params = QueryProductDetailsParams.newBuilder().setProductList(productList).build()
        client.queryProductDetailsAsync(params) { result, detailsList ->
            if (result.responseCode == BillingClient.BillingResponseCode.OK) {
                val d = detailsList.firstOrNull { it.productId == sku }
                if (d != null) cachedProductDetails = d
                onDone(d)
            } else {
                Log.w(tag, "queryProductDetails failed: ${result.responseCode} ${result.debugMessage}")
                onDone(null)
            }
        }
    }

    /** Restore уже куплених підписок (grant entitlement без repeat-запиту). */
    fun queryPurchases() {
        val client = billingClient
        if (client == null || !client.isReady) return
        val params = QueryPurchasesParams.newBuilder()
            .setProductType(BillingClient.ProductType.SUBS)
            .build()
        client.queryPurchasesAsync(params) { result, purchasesList ->
            if (result.responseCode != BillingClient.BillingResponseCode.OK) return@queryPurchasesAsync
            // Якщо є активна неврахована підписка — acknowledge + повідомляємо JS
            val active = purchasesList.firstOrNull {
                it.purchaseState == Purchase.PurchaseState.PURCHASED &&
                    it.products.contains(skuId)
            }
            if (active != null) {
                if (!active.isAcknowledged) {
                    acknowledge(active.purchaseToken)
                }
                onResult(true, active.purchaseToken)
            }
        }
    }

    /** Підтвердження підписки — обов'язково протягом 3 днів, інакше Google повертає гроші. */
    private fun acknowledge(purchaseToken: String) {
        val client = billingClient ?: return
        val params = AcknowledgePurchaseParams.newBuilder()
            .setPurchaseToken(purchaseToken)
            .build()
        client.acknowledgePurchase(params) { result ->
            Log.i(tag, "acknowledge → ${result.responseCode}")
        }
    }

    private fun handlePurchase(purchase: Purchase) {
        purchaseInProgress = false
        if (purchase.purchaseState != Purchase.PurchaseState.PURCHASED) {
            // Pending (cash/deferred) — чекаємо, premium не видаємо
            onResult(false, "pending")
            return
        }
        if (!purchase.products.contains(skuId)) {
            // Чужий SKU — ігноруємо
            return
        }
        // Підтверджуємо (якщо ще ні) → повідомляємо колбек
        if (!purchase.isAcknowledged) {
            acknowledge(purchase.purchaseToken)
        }
        onResult(true, purchase.purchaseToken)
    }
}
