"""
Created on 28-Mar-2018

@author: tanzeem
"""
import uuid
import json
import datetime

from couchbase_helper.documentgenerator import DocumentGenerator
from membase.helper.cluster_helper import ClusterOperationHelper

from BucketLib.BucketOperations import BucketHelper
from cbas.cbas_base import CBASBaseTest
from sdk_client import SDKClient


class CBASBacklogIngestion(CBASBaseTest):

    def setUp(self):
        super(CBASBacklogIngestion, self).setUp()

    @staticmethod
    def generate_documents(start_at, end_at, role=None):
        age = range(70)
        first = ['james', 'sharon', 'dave', 'bill', 'mike', 'steve']
        if role is None:
            profession = ['teacher']
        else:
            profession = role
        template = '{{ "number": {0}, "first_name": "{1}" , "profession":"{2}", "mutated":0}}'
        documents = DocumentGenerator('test_docs', template, age, first, profession, start=start_at, end=end_at)
        return documents

    '''
    -i b/resources/4-nodes-template.ini -t cbas.cbas_backlog_ingestion.CBASBacklogIngestion.test_document_expiry_with_overlapping_filters_between_datasets,default_bucket=True,items=10000,cb_bucket_name=default,cbas_bucket_name=default_cbas,cbas_dataset_name=default_ds,where_field=profession,where_value=teacher,batch_size=10000
    -i b/resources/4-nodes-template.ini -t cbas.cbas_backlog_ingestion.CBASBacklogIngestion.test_document_expiry_with_overlapping_filters_between_datasets,default_bucket=True,items=10000,cb_bucket_name=default,cbas_bucket_name=default_cbas,cbas_dataset_name=default_ds,where_field=profession,where_value=teacher,batch_size=10000,secondary_index=True,index_fields=profession:string
    '''

    def test_document_expiry_with_overlapping_filters_between_datasets(self):
        """
        1. Create default bucket
        2. Load data with profession doctor and lawyer
        3. Load data with profession teacher
        4. Create dataset with no filters
        5. Create filter dataset that holds data with profession teacher
        6. Verify the dataset count
        7. Delete half the documents with profession teacher
        8. Verify the updated dataset count
        9. Expire data with profession teacher
        10. Verify the updated dataset count post expiry
        """
        self.log.info("Set expiry pager on default bucket")
        ClusterOperationHelper.flushctl_set(self.master, "exp_pager_stime", 1, bucket="default")

        self.log.info("Load data in the default bucket")
        num_items = self.input.param("items", 10000)
        batch_size = self.input.param("batch_size", 10000)
        self.perform_doc_ops_in_all_cb_buckets(self.num_items, "create", 0, num_items, exp=0, batch_size=batch_size)

        self.log.info("Load data in the default bucket, with documents containing profession teacher")
        load_gen = CBASBacklogIngestion.generate_documents(num_items, num_items * 2, role=['teacher'])
        self._async_load_all_buckets(server=self.master, kv_gen=load_gen, op_type="create", exp=0, batch_size=batch_size)

        self.log.info("Create primary index")
        query = "CREATE PRIMARY INDEX ON {0} using gsi".format(self.buckets[0].name)
        self.rest.query_tool(query)

        self.log.info("Create a connection")
        cb_bucket_name = self.input.param("cb_bucket_name")
        self.cbas_util.createConn(cb_bucket_name)

        self.log.info("Create a CBAS bucket")
        cbas_bucket_name = self.input.param("cbas_bucket_name")
        self.cbas_util.create_bucket_on_cbas(cbas_bucket_name=cbas_bucket_name,
                                             cb_bucket_name=cb_bucket_name)

        self.log.info("Create a default data-set")
        cbas_dataset_name = self.input.param("cbas_dataset_name")
        self.cbas_util.create_dataset_on_bucket(cbas_bucket_name=cbas_bucket_name,
                                                cbas_dataset_name=cbas_dataset_name)

        self.log.info("Read input params for field name and value")
        field = self.input.param("where_field", "")
        value = self.input.param("where_value", "")
        cbas_dataset_with_clause = cbas_dataset_name + "_" + value

        self.log.info("Create data-set with profession teacher")
        self.cbas_util.create_dataset_on_bucket(cbas_bucket_name=cbas_bucket_name,
                                                cbas_dataset_name=cbas_dataset_with_clause,
                                                where_field=field, where_value=value)

        secondary_index = self.input.param("secondary_index", False)
        datasets = [cbas_dataset_name, cbas_dataset_with_clause]
        index_fields = self.input.param("index_fields", None)
        if secondary_index:
            self.log.info("Create secondary index")
            index_fields = ""
            for index_field in self.index_fields:
                index_fields += index_field + ","
                index_fields = index_fields[:-1]
            for dataset in datasets:
                create_idx_statement = "create index {0} on {1}({2});".format(
                    dataset + "_idx", dataset, index_fields)
                status, metrics, errors, results, _ = self.cbas_util.execute_statement_on_cbas_util(
                    create_idx_statement)

                self.assertTrue(status == "success", "Create Index query failed")
                self.assertTrue(self.cbas_util.verify_index_created(dataset + "_idx", self.index_fields,
                                                                    dataset)[0])

        self.log.info("Connect to CBAS bucket")
        self.cbas_util.connect_to_bucket(cbas_bucket_name=cbas_bucket_name,
                                         cb_bucket_password=self.cb_bucket_password)

        self.log.info("Wait for ingestion to complete on both data-sets")
        self.cbas_util.wait_for_ingestion_complete([cbas_dataset_name], num_items * 2)
        self.cbas_util.wait_for_ingestion_complete([cbas_dataset_with_clause], num_items)

        self.log.info("Validate count on data-set")
        self.assertTrue(self.cbas_util.validate_cbas_dataset_items_count(cbas_dataset_name, num_items * 2))
        self.assertTrue(self.cbas_util.validate_cbas_dataset_items_count(cbas_dataset_with_clause, num_items))

        self.log.info("Delete half of the teacher records")
        self.perform_doc_ops_in_all_cb_buckets(num_items // 2, "delete", num_items + (num_items // 2), num_items * 2)

        self.log.info("Wait for ingestion to complete")
        self.cbas_util.wait_for_ingestion_complete([cbas_dataset_name], num_items + (num_items // 2))
        self.cbas_util.wait_for_ingestion_complete([cbas_dataset_with_clause], num_items // 2)

        self.log.info("Validate count on data-set")
        self.assertTrue(
            self.cbas_util.validate_cbas_dataset_items_count(cbas_dataset_name, num_items + (num_items // 2)))
        self.assertTrue(self.cbas_util.validate_cbas_dataset_items_count(cbas_dataset_with_clause, num_items // 2))

        self.log.info("Update the documents with profession teacher to expire in next 1 seconds")
        self.perform_doc_ops_in_all_cb_buckets(num_items // 2, "update", num_items, num_items + (num_items // 2), exp=1)
        
        self.log.info("Wait for documents to expire")
        self.sleep(15, message="Waiting for documents to expire")
        
        self.log.info("Wait for ingestion to complete")
        self.cbas_util.wait_for_ingestion_complete([cbas_dataset_name], num_items)
        self.cbas_util.wait_for_ingestion_complete([cbas_dataset_with_clause], 0)

        self.log.info("Validate count on data-set")
        self.assertTrue(self.cbas_util.validate_cbas_dataset_items_count(cbas_dataset_name, num_items))
        self.assertTrue(self.cbas_util.validate_cbas_dataset_items_count(cbas_dataset_with_clause, 0))

    '''
    -i b/resources/4-nodes-template.ini -t cbas.cbas_backlog_ingestion.CBASBacklogIngestion.test_multiple_cbas_bucket_with_overlapping_filters_between_datasets,default_bucket=True,
    cb_bucket_name=default,cbas_bucket_name=default_cbas_,num_of_cbas_buckets=4,items=10000,cbas_dataset_name=default_ds_,where_field=profession,join_operator=or,batch_size=10000
    '''

    def test_multiple_cbas_bucket_with_overlapping_filters_between_datasets(self):
        """
        1. Create default bucket
        2. Load data in default bucket with professions picked from the predefined list
        3. Create CBAS bucket and a dataset in each CBAS bucket such that dataset between the cbas buckets have overlapping filter
        4. Verify dataset count
        """
        self.log.info("Load data in the default bucket")
        num_of_cbas_buckets = self.input.param("num_of_cbas_buckets", 4)
        batch_size = self.input.param("batch_size", 10000)
        cbas_bucket_name = self.input.param("cbas_bucket_name", "default_cbas_")
        professions = ['teacher', 'doctor', 'engineer', 'dentist', 'racer', 'dancer', 'singer', 'musician', 'pilot',
                       'finance']
        load_gen = CBASBacklogIngestion.generate_documents(0, self.num_items, role=professions[:num_of_cbas_buckets])
        self._async_load_all_buckets(server=self.master, kv_gen=load_gen, op_type="create", exp=0, batch_size=batch_size)

        self.log.info("Create primary index")
        query = "CREATE PRIMARY INDEX ON {0} using gsi".format(self.buckets[0].name)
        self.rest.query_tool(query)

        self.log.info("Create connection")
        self.cbas_util.createConn(self.cb_bucket_name)

        self.log.info("Create CBAS buckets")
        num_of_cbas_buckets = self.input.param("num_of_cbas_buckets", 2)
        for index in range(num_of_cbas_buckets):
            self.assertTrue(self.cbas_util.create_bucket_on_cbas(cbas_bucket_name=cbas_bucket_name + str(index),
                                                                 cb_bucket_name=self.cb_bucket_name),
                            "Failed to create cbas bucket " + self.cbas_bucket_name + str(index))

        self.log.info("Create data-sets")
        field = self.input.param("where_field", "")
        join_operator = self.input.param("join_operator", "or")
        for index in range(num_of_cbas_buckets):
            tmp = "\"" + professions[index] + "\" "
            join_values = professions[index + 1:index + 2]
            for join_value in join_values:
                tmp += join_operator + " `" + field + "`=\"" + join_value + "\""
            tmp = tmp[1:-1]
            self.assertTrue(self.cbas_util.create_dataset_on_bucket((self.cbas_bucket_name + str(index)),
                                                                    cbas_dataset_name=(self.cbas_dataset_name + str(
                                                                        index)),
                                                                    where_field=field, where_value=tmp))

        self.log.info("Connect to CBAS bucket")
        for index in range(num_of_cbas_buckets):
            self.cbas_util.connect_to_bucket(cbas_bucket_name=cbas_bucket_name + str(index),
                                             cb_bucket_password=self.cb_bucket_password)

        self.log.info("Wait for ingestion to completed and assert count")
        for index in range(num_of_cbas_buckets):
            n1ql_query = 'select count(*) from `{0}` where {1} = "{2}"'.format(self.cb_bucket_name, field,
                                                                               professions[index])
            tmp = " "
            join_values = professions[index + 1:index + 2]
            for join_value in join_values:
                tmp += join_operator + " `" + field + "`=\"" + join_value + "\""
            n1ql_query += tmp
            count_n1ql = self.rest.query_tool(n1ql_query)['results'][0]['$1']
            self.cbas_util.wait_for_ingestion_complete([self.cbas_dataset_name + str(index)], count_n1ql)
            _, _, _, results, _ = self.cbas_util.execute_statement_on_cbas_util(
                'select count(*) from `%s`' % (self.cbas_dataset_name + str(index)))
            count_ds = results[0]["$1"]
            self.assertEqual(count_ds, count_n1ql, msg="result count mismatch between N1QL and Analytics")


class BucketOperations(CBASBaseTest):
    CBAS_BUCKET_CONNECT_ERROR_MSG = "Maximum number of active writable datasets (8) exceeded"

    def setUp(self):
        super(BucketOperations, self).setUp()

    def fetch_test_case_arguments(self):
        self.cb_bucket_name = self.input.param("cb_bucket_name", "default")
        self.cbas_bucket_name = self.input.param("cbas_bucket_name", "default_cbas")
        self.dataset_prefix = self.input.param("dataset_prefix", "_ds_")
        self.num_of_dataset = self.input.param("num_of_dataset", 9)
        self.num_items = self.input.param("items", 10000)
        self.dataset_name = self.input.param("dataset_name", "ds")
        self.num_of_cb_buckets = self.input.param("num_of_cb_buckets", 8)
        self.num_of_cbas_buckets_per_cb_bucket = self.input.param("num_of_cbas_buckets_per_cb_bucket", 2)
        self.num_of_dataset_per_cbas = self.input.param("num_of_dataset_per_cbas", 8)
        self.default_bucket = self.input.param("default_bucket", False)
        self.cbas_bucket_prefix = self.input.param("cbas_bucket_prefix", "_cbas_bucket_")

    '''
    test_cbas_bucket_connect_with_more_than_eight_active_datasets,default_bucket=True,cb_bucket_name=default,cbas_bucket_name=default_cbas,dataset_prefix=_ds_,num_of_dataset=9,items=10   
    '''

    def test_cbas_bucket_connect_with_more_than_eight_active_datasets(self):
        """
        1. Create a cb bucket
        2. Create a cbas bucket
        3. Create 9 datasets
        4. Connect to cbas bucket must fail with error - Maximum number of active writable datasets (8) exceeded
        5. Delete 1 dataset, now the count must be 8
        6. Re-connect the cbas bucket and this time connection must succeed
        7. Verify count in dataset post connect
        """
        self.log.info("Fetch test case arguments")
        self.fetch_test_case_arguments()

        self.log.info("Load data in the default bucket")
        self.perform_doc_ops_in_all_cb_buckets(self.num_items, "create", 0, self.num_items)

        self.log.info("Create reference to SDK client")
        client = SDKClient(scheme="couchbase", hosts=[self.master.ip], bucket=self.cb_bucket_name,
                           password=self.master.rest_password)

        self.log.info("Insert binary data into default bucket")
        keys = ["%s" % (uuid.uuid4()) for i in range(0, self.num_items)]
        client.insert_binary_document(keys)

        self.log.info("Insert Non-Json string data into default bucket")
        keys = ["%s" % (uuid.uuid4()) for i in range(0, self.num_items)]
        client.insert_string_document(keys)

        self.log.info("Create connection")
        self.cbas_util.createConn(self.cb_bucket_name)

        self.log.info("Create a CBAS bucket")
        self.assertTrue(self.cbas_util.create_bucket_on_cbas(cbas_bucket_name=self.cbas_bucket_name,
                                                             cb_bucket_name=self.cb_bucket_name),
                        msg="Failed to create CBAS bucket")

        self.log.info("Create datasets")
        for i in range(1, self.num_of_dataset + 1):
            self.assertTrue(self.cbas_util.create_dataset_on_bucket(cbas_bucket_name=self.cbas_bucket_name,
                                                                    cbas_dataset_name=self.dataset_prefix + str(i)),
                            msg="Failed to create dataset {0}".format(self.dataset_prefix + str(i)))

        self.log.info("Verify connect to CBAS bucket must fail")
        self.assertTrue(self.cbas_util.connect_to_bucket(cbas_bucket_name=self.cbas_bucket_name,
                                                         cb_bucket_password=self.cb_bucket_password,
                                                         validate_error_msg=True,
                                                         expected_error=BucketOperations.CBAS_BUCKET_CONNECT_ERROR_MSG),
                        msg="Incorrect error msg while connecting to cbas bucket")

        self.log.info("Drop the last dataset created")
        self.assertTrue(self.cbas_util.drop_dataset(cbas_dataset_name=self.dataset_prefix + str(self.num_of_dataset)),
                        msg="Failed to drop dataset {0}".format(self.dataset_prefix + str(self.num_of_dataset)))

        self.log.info("Connect to CBAS bucket")
        self.assertTrue(self.cbas_util.connect_to_bucket(cbas_bucket_name=self.cbas_bucket_name),
                        msg="Failed to connect to cbas bucket")

        self.log.info("Wait for ingestion to complete and validate count")
        for i in range(1, self.num_of_dataset):
            self.cbas_util.wait_for_ingestion_complete([self.dataset_prefix + str(i)], self.num_items)
            self.assertTrue(
                self.cbas_util.validate_cbas_dataset_items_count(self.dataset_prefix + str(i), self.num_items))

    '''
    test_delete_cb_bucket_with_cbas_connected,default_bucket=True,cb_bucket_name=default,cbas_bucket_name=default_cbas,dataset_name=ds,items=10
    '''

    def test_delete_cb_bucket_with_cbas_connected(self):
        """
        1. Create a cb bucket
        2. Create a cbas bucket
        3. Create a dataset
        4. Connect to cbas bucket
        5. Verify count on dataset
        6. Delete the cb bucket
        7. Verify count on dataset must remain unchange
        8. Recreate the cb bucket
        9. Verify count on dataset must be 0
        9. Insert documents double the initial size
        9. Verify count on dataset post cb create
        """
        self.log.info("Fetch test case arguments")
        self.fetch_test_case_arguments()

        self.log.info("Load data in the default bucket")
        self.perform_doc_ops_in_all_cb_buckets(self.num_items, "create", 0, self.num_items)

        self.log.info("Create connection")
        self.cbas_util.createConn(self.cb_bucket_name)

        self.log.info("Create a CBAS bucket")
        self.assertTrue(self.cbas_util.create_bucket_on_cbas(cbas_bucket_name=self.cbas_bucket_name,
                                                             cb_bucket_name=self.cb_bucket_name),
                        msg="Failed to create CBAS bucket")

        self.log.info("Create datasets")
        self.assertTrue(self.cbas_util.create_dataset_on_bucket(cbas_bucket_name=self.cbas_bucket_name,
                                                                cbas_dataset_name=self.dataset_name),
                        msg="Failed to create dataset {0}".format(self.dataset_name))

        self.log.info("Connect to CBAS bucket")
        self.assertTrue(self.cbas_util.connect_to_bucket(cbas_bucket_name=self.cbas_bucket_name),
                        msg="Failed to connect to cbas bucket")

        self.log.info("Wait for ingestion to complete and verify count")
        self.cbas_util.wait_for_ingestion_complete([self.dataset_name], self.num_items)
        self.assertTrue(self.cbas_util.validate_cbas_dataset_items_count(self.dataset_name, self.num_items))

        self.log.info("Delete CB bucket")
        self.delete_bucket_or_assert(serverInfo=self.master)

        self.log.info("Verify count on dataset")
        self.assertTrue(self.cbas_util.validate_cbas_dataset_items_count(self.dataset_name, self.num_items))

        self.log.info("Recreate CB bucket")
        self.create_default_bucket()

        self.log.info("Wait for ingestion to complete and verify count")
        self.cbas_util.wait_for_ingestion_complete([self.dataset_name], 0)
        self.assertTrue(self.cbas_util.validate_cbas_dataset_items_count(self.dataset_name, 0))

        self.log.info("Load back data in the default bucket")
        self.perform_doc_ops_in_all_cb_buckets(self.num_items * 2, "create", 0, self.num_items * 2)

        self.log.info("Wait for ingestion to complete and verify count")
        self.cbas_util.wait_for_ingestion_complete([self.dataset_name], self.num_items * 2)
        self.assertTrue(self.cbas_util.validate_cbas_dataset_items_count(self.dataset_name, self.num_items * 2))

    '''
    test_create_multiple_cb_cbas_and_datasets,num_of_cb_buckets=8,num_of_cbas_buckets_per_cb_bucket=2,num_of_dataset_per_cbas=8,default_bucket=False,cbas_bucket_prefix=_cbas_bucket_,dataset_prefix=_ds_,items=10
    '''

    def test_create_multiple_cb_cbas_and_datasets(self):

        self.log.info("Fetch test case arguments")
        self.fetch_test_case_arguments()

        self.log.info("Fetch and set memory quota")
        memory_for_kv = int(self.fetch_available_memory_for_kv_on_a_node())
        self.rest.set_service_memoryQuota(service='memoryQuota', memoryQuota=memory_for_kv)

        self.log.info("Create {0} cb buckets".format(self.num_of_cb_buckets))
        self.create_multiple_buckets(server=self.master, replica=1, howmany=self.num_of_cb_buckets)

        self.log.info("Check if buckets are created")
        bucket_helper = BucketHelper(self.master)
        buckets = bucket_helper.get_buckets()
        self.assertEqual(len(buckets), self.num_of_cb_buckets, msg="CB bucket count mismatch")

        self.log.info("Load data in the default bucket")
        self.perform_doc_ops_in_all_cb_buckets(self.num_items, "create", 0, self.num_items)

        self.log.info("Create connection to all buckets")
        for bucket in buckets:
            self.cbas_util.createConn(bucket.name)

        self.log.info("Create {0} cbas buckets".format(self.num_of_cb_buckets * self.num_of_cbas_buckets_per_cb_bucket))
        for bucket in buckets:
            for index in range(self.num_of_cbas_buckets_per_cb_bucket):
                self.assertTrue(
                    self.cbas_util.create_bucket_on_cbas(
                        cbas_bucket_name=bucket.name.replace("-", "_") + self.cbas_bucket_prefix + str(index),
                        cb_bucket_name=bucket.name),
                    msg="Failed to create CBAS bucket")

        self.log.info("Check if cbas buckets are created")
        cbas_buckets = []
        _, _, _, results, _ = self.cbas_util.execute_statement_on_cbas_util("select Name from Metadata.`Bucket`")
        for row in results:
            cbas_buckets.append(row['Name'])
        self.assertEqual(len(cbas_buckets), self.num_of_cb_buckets * self.num_of_cbas_buckets_per_cb_bucket,
                         msg="CBAS bucket count mismatch")

        self.log.info("Create {0} datasets".format(
            self.num_of_cb_buckets * self.num_of_cbas_buckets_per_cb_bucket * self.num_of_dataset_per_cbas))
        for cbas_bucket in cbas_buckets:
            for index in range(self.num_of_dataset_per_cbas):
                self.assertTrue(self.cbas_util.create_dataset_on_bucket(cbas_bucket_name=cbas_bucket,
                                                                        cbas_dataset_name=
                                                                        cbas_bucket + self.dataset_prefix + str(index)),
                                msg="Failed to create dataset {0}".format(self.dataset_name))

        self.log.info("Update storageMaxActiveWritableDatasets count")
        active_data_set_count = self.num_of_cb_buckets * self.num_of_cbas_buckets_per_cb_bucket * self.num_of_dataset_per_cbas
        status, _, _ = self.cbas_util.update_config_on_cbas(config_name="storageMaxActiveWritableDatasets",
                                                            config_value=active_data_set_count)
        self.assertTrue(status, msg="Failed to update config")

        self.log.info("Restart the cbas node using the api")
        status, _, _ = self.cbas_util.restart_cbas()
        self.assertTrue(status, msg="Failed to restart cbas")

        self.log.info("Wait for node restart and assert on storageMaxActiveWritableDatasets count")
        minutes_to_run = self.input.param("minutes_to_run", 1)
        end_time = datetime.datetime.now() + datetime.timedelta(minutes=int(minutes_to_run))
        active_dataset = None
        self.sleep(30, message="wait for server to be up")
        while datetime.datetime.now() < end_time and active_dataset is None:
            try:
                status, content, response = self.cbas_util.fetch_config_on_cbas()
                print(status, content, response)
                config_dict = json.loads((content.decode("utf-8")))
                active_dataset = config_dict['storageMaxActiveWritableDatasets']
            except Exception:
                self.sleep(10, message="waiting for server to be up")
        self.assertEqual(active_dataset, active_data_set_count, msg="Value in correct for active dataset count")

        self.log.info("Connect to CBAS buckets and assert document count")
        for cbas_bucket in cbas_buckets:
            self.assertTrue(self.cbas_util.connect_to_bucket(cbas_bucket_name=cbas_bucket),
                            msg="Failed to connect to cbas bucket")
            for index in range(self.num_of_dataset_per_cbas):
                self.log.info("Wait for ingestion to complete and verify count")
                self.cbas_util.wait_for_ingestion_complete([cbas_bucket + self.dataset_prefix + str(index)],
                                                           self.num_items)
                self.assertTrue(
                    self.cbas_util.validate_cbas_dataset_items_count(cbas_bucket + self.dataset_prefix + str(index),
                                                                     self.num_items))

def tearDown(self):
    super(BucketOperations, self).setUp()
